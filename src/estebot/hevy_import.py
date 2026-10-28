#!/usr/bin/env python3
"""Jefit history -> Hevy, through the API. A one-off, run by hand in stages.

    python -m estebot.hevy_import                          dry run: build all, post nothing
    python -m estebot.hevy_import --post --date YYYY-MM-DD post one day (the pilot)
    python -m estebot.hevy_import --post --limit 10        post the 10 oldest not yet posted
    python -m estebot.hevy_import --post                   post the rest

The source is the tidy sets.csv, not the raw export, so the history lands in Hevy with the
renames and dumbbell corrections already applied. Exercise names go through the data repo's
jefit_to_hevy.csv, which must say ok or skip for every exercise before anything is built.
Jefit's raw export supplies what the tidy table drops: session start/end times and the order
exercises were logged in.

Hevy has no delete endpoint, so a wrong post is cleaned up by hand in the app. Hence:
  - every workout is private and its description starts with hevy.IMPORT_TAG, which the
    Hevy reader skips, so imported history never comes back into the tidy tables twice
  - a day that already has any Hevy workout is never posted, checked against a fresh pull
    right before posting, so a re-run after a crash can't duplicate a day
  - each post is appended to hevy_import_log.csv the moment it succeeds
Stdlib only.
"""
import argparse, csv, json, time, urllib.error, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from estebot.parsers import hevy
from estebot.parsers.jefit_raw import load_renames, merged_sections, norm_name, rename

LB_TO_KG = 0.45359237          # exact; hevy.kg_to_lb recovers the typed pound value
MAX_SESSION_MIN = 240          # a longer Jefit session is a timer left running
FALLBACK_MIN = 60
POST_INTERVAL_S = 1.5          # no documented rate limit; stay well clear of one
TYPES = {"weight_reps", "reps_only", "bodyweight_weighted", "short_distance_weight"}
CARRY_NOTE = "Jefit logged carries as a rough distance; posted as metres."


class MapError(ValueError):
    pass


# ---------------------------------------------------------------- inputs
def load_map(path):
    with open(path, newline="") as f:
        return {r["jefit_exercise"]: r for r in csv.DictReader(f)}

def load_overrides(path):
    try:
        with open(path, newline="") as f:
            return {(r["date"], r["exercise"], int(r["set_idx"])): (r["set_type"], int(r["reps"]))
                    for r in csv.DictReader(f)}
    except FileNotFoundError:
        return {}

def raw_context(paths, renames):
    """Jefit export -> (sessions, order).
    sessions: date -> [(start_epoch, end_epoch)]. order: (date, exercise) -> first log id,
    with exercise named as the tidy table names it."""
    S = merged_sections(paths)
    sessions = defaultdict(list)
    for r in S.get("WORKOUT SESSIONS", []):
        try:
            a, b = int(r["starttime"]), int(r["endtime"] or 0)
        except (KeyError, ValueError):
            continue
        sessions[datetime.fromtimestamp(a).date().isoformat()].append((a, b))
    order = {}
    for r in S.get("EXERCISE LOGS", []):
        d = r.get("mydate")
        try:
            i = int(r["_id"])
        except (KeyError, ValueError):
            continue
        k = (d, rename(renames, d, norm_name(r["ename"])))
        order[k] = min(order.get(k, i), i)
    return dict(sessions), order


# ---------------------------------------------------------------- build (pure)
def times(spans):
    """-> (start, end, fell_back). Start is the day's first session; the length is the sum
    of its sessions, ignoring any that ran past MAX_SESSION_MIN."""
    if not spans:
        return None, None, True
    start = min(a for a, _ in spans)
    secs = sum(b - a for a, b in spans if 0 < b - a <= MAX_SESSION_MIN * 60)
    fell_back = not 0 < secs <= MAX_SESSION_MIN * 60
    if fell_back:
        secs = FALLBACK_MIN * 60
    return start, start + secs, fell_back

def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def hevy_set(row, m, override=None):
    """One tidy set row -> one Hevy set, shaped by the Hevy exercise's type."""
    lb, reps = float(row["weight_lb"]), int(row["reps"])
    kind = m["hevy_type"]
    s = {"type": "normal", "weight_kg": None, "reps": None, "distance_meters": None,
         "duration_seconds": None, "custom_metric": None, "rpe": None}
    if kind == "reps_only":
        s["reps"] = reps                      # any Jefit weight here is its placeholder
    elif kind == "short_distance_weight":
        s["weight_kg"], s["distance_meters"] = lb * LB_TO_KG, reps
    else:
        if m.get("placeholder_lb") and lb == float(m["placeholder_lb"]):
            lb = 0.0
        s["weight_kg"], s["reps"] = lb * LB_TO_KG, reps
    if override:
        s["type"], s["reps"] = override
    return s

def build(rows, mapping, sessions, order, overrides=None, skip_dates=()):
    """Tidy Jefit set rows -> ([(date, payload)], report). Raises MapError if any exercise
    is missing from the map or not yet settled."""
    overrides = overrides or {}
    bad = sorted({r["exercise"] for r in rows
                  if mapping.get(r["exercise"], {}).get("status") not in ("ok", "skip")})
    if bad:
        raise MapError("not settled in jefit_to_hevy.csv: " + ", ".join(bad))
    for m in mapping.values():
        if m["status"] == "ok" and m["hevy_type"] not in TYPES:
            raise MapError(f"{m['jefit_exercise']}: no rule for Hevy type {m['hevy_type']!r}")

    rep = defaultdict(int)
    by_day = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if mapping[r["exercise"]]["status"] == "skip":
            rep["sets skipped (exercise not migrated)"] += 1; continue
        by_day[r["date"]][r["exercise"]].append(r)

    out = []
    for day in sorted(by_day):
        if day in skip_dates:
            rep["days already in Hevy"] += 1; continue
        start, end, fell_back = times(sessions.get(day, []))
        if start is None:
            start = int(datetime.fromisoformat(day + "T12:00:00").timestamp())
            end = start + FALLBACK_MIN * 60
        rep["days with estimated duration"] += fell_back
        exs, groups = [], []
        for name in sorted(by_day[day], key=lambda n: (order.get((day, n), float("inf")), n)):
            m = mapping[name]
            sets = []
            for r in sorted(by_day[day][name], key=lambda r: int(r["set_idx"])):
                o = overrides.get((day, name, int(r["set_idx"])))
                rep["sets overridden"] += o is not None
                if (m.get("placeholder_lb") and m["hevy_type"] != "reps_only"
                        and float(r["weight_lb"]) == float(m["placeholder_lb"])):
                    rep["placeholder loads posted as 0"] += 1
                sets.append(hevy_set(r, m, o))
            rep["sets"] += len(sets)
            rep[f"sets as {m['hevy_type']}"] += len(sets)
            exs.append({"exercise_template_id": m["hevy_template_id"], "superset_id": None,
                        "notes": CARRY_NOTE if m["hevy_type"] == "short_distance_weight" else None,
                        "sets": sets})
            g = by_day[day][name][0].get("muscle_group", "")
            if g and g not in groups:
                groups.append(g)
        title = " + ".join(g.capitalize() for g in groups[:3]) or "Workout"
        out.append((day, {"workout": {
            "title": title,
            "description": f"{hevy.IMPORT_TAG} (Jefit, {day})",
            "start_time": iso(start), "end_time": iso(end),
            "is_private": True,
            "exercises": exs}}))
    rep["days"] = len(out)
    return out, dict(rep)


# ---------------------------------------------------------------- post
def _post(path, key, body):
    req = urllib.request.Request(f"{hevy.API}{path}", data=json.dumps(body).encode(),
                                 method="POST", headers={"api-key": key, "accept": "application/json",
                                                         "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

LOG_COLS = ["date", "hevy_id", "start_time", "exercises", "sets", "posted_at"]

def posted_dates(log_path):
    try:
        with open(log_path, newline="") as f:
            return {r["date"] for r in csv.DictReader(f)}
    except FileNotFoundError:
        return set()

def _log(log_path, row):
    new = not log_path.exists()
    with open(log_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new: w.writeheader()
        w.writerow(row)


def main():
    from estebot import config
    from estebot.core import order_by_name_date
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--post", action="store_true", help="actually post (default: dry run)")
    ap.add_argument("--date", help="only this day (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int, help="post at most this many days, oldest first")
    a = ap.parse_args()

    cfg = config.load()
    with open(cfg.tidy / "sets.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["source"] == "jefit"]
    mapping = load_map(cfg.data_file("jefit_to_hevy.csv"))
    sessions, order = raw_context(order_by_name_date([str(p) for p in cfg.all_exports("jefit")]),
                                  load_renames(str(cfg.data_file("renames.csv"))))
    log_path = cfg.data_file("hevy_import_log.csv")

    if a.post:
        key = hevy.api_key(cfg)
        in_hevy = {hevy.local_date(w["start_time"]) for w in hevy.fetch_workouts(key)}
    else:
        key, in_hevy = None, {hevy.local_date(w["start_time"]) for w in hevy.load_snapshot(cfg)}
    skip = in_hevy | posted_dates(log_path)

    workouts, rep = build(rows, mapping, sessions, order,
                          load_overrides(cfg.data_file("hevy_import_overrides.csv")), skip)
    if a.date:
        workouts = [w for w in workouts if w[0] == a.date]
        if not workouts:
            raise SystemExit(f"{a.date}: nothing to post (no Jefit sets, or already in Hevy)")
    if a.limit:
        workouts = workouts[:a.limit]

    for k in sorted(rep):
        print(f"   {k}: {rep[k]}")
    if not a.post:
        preview = hevy.snapshot_path(cfg).with_name("import_preview.json")
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(json.dumps([p for _, p in workouts], indent=1))
        print(f"dry run: {len(workouts)} workouts built, nothing posted. Payloads -> {preview}")
        return

    print(f"posting {len(workouts)} workout(s), private, {POST_INTERVAL_S}s apart")
    for n, (day, payload) in enumerate(workouts, 1):
        try:
            res = _post("/workouts", key, payload)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{day}: HTTP {e.code} {e.read().decode(errors='replace')}\n"
                             f"stopped after {n - 1} posted; re-run to continue")
        w = res["workout"][0] if isinstance(res.get("workout"), list) else res.get("workout", {})
        ex = payload["workout"]["exercises"]
        _log(log_path, {"date": day, "hevy_id": w.get("id", ""),
                        "start_time": payload["workout"]["start_time"], "exercises": len(ex),
                        "sets": sum(len(e["sets"]) for e in ex),
                        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        print(f"   {n}/{len(workouts)} {day} {payload['workout']['title']} -> {w.get('id', '?')}")
        if n < len(workouts):
            time.sleep(POST_INTERVAL_S)


if __name__ == "__main__":
    main()

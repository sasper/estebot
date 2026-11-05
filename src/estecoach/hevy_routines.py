#!/usr/bin/env python3
"""Today's plan -> the matching Hevy routine, so the session starts prefilled. Stdlib only.

What is written, and what never is (design doc "EsteCoach: prefilling Hevy routines"):

  written   routines only. On a normal day each planned exercise's top set (the normal sets
            at the routine's heaviest weight) gets the target weight and its rep range, reps
            left empty so what is logged is what was done; one "EsteCoach:" line in the
            exercise's note. On a take-it-easy day the exercise's sets are cut to the
            deload (core.deload), and the next write puts the sets from before it back.
  never     a workout (create, edit or delete): Hevy workouts are the logged history
            EsteCoach grades against. Exercise order, supersets, rest times, set types, the
            routine's title or folder. Never from the demo, presentation mode or the chat.

Every request goes through _request, which allows reading routines and updating one, and
raises on anything else. Every write is logged to the data repo's hevy_writes.csv with the
whole routine as it was, so any prefill can be traced and undone (--restore).

  python -m estecoach.hevy_routines --link            list routines, print the config block
  python -m estecoach.hevy_routines --check <id>      read -> unchanged update -> read, diffed
  python -m estecoach.hevy_routines --preview         what today's write would change
  python -m estecoach.hevy_routines --send            write today's plan now
  python -m estecoach.hevy_routines --restore <date>  put back the routine as before that day's write
"""
import copy, csv, json, re, sys, time, urllib.request
from datetime import date, datetime

from estecoach import core
from estecoach.parsers import hevy
from estecoach.parsers.jefit_raw import load_corrections, load_renames, rename

LOG = "hevy_writes.csv"
LOG_FIELDS = ("date", "written_at", "source", "routine_id", "routine", "plan", "verdict",
              "deload", "changes", "before")
NOTE = "EsteCoach:"
OLD_NOTES = ("EsteBot:",)   # lines written before the rename, replaced like our own
HEAVY = 0.8     # a set at 80% or more of the top weight is a heavy set, not a ramp
VERDICT_TEXT = {"hard": "Go hard", "normal": "Go as planned", "light": "Take it easy", "rest": "Rest day"}

# What a routine read returns that an update doesn't take. Anything else that turns up is a
# field Hevy added, which a rebuilt update would silently drop, so put_body refuses it.
ROUTINE_KEYS, ROUTINE_READ_ONLY = {"title", "folder_id", "exercises"}, {"id", "created_at", "updated_at"}
EXERCISE_KEYS, EXERCISE_READ_ONLY = {"exercise_template_id", "superset_id", "rest_seconds", "notes", "sets"}, {"index", "title"}
SET_KEYS, SET_READ_ONLY = {"type", "weight_kg", "reps", "distance_meters", "duration_seconds",
                           "custom_metric", "rep_range"}, {"index"}

_ID = r"[0-9a-fA-F-]{36}"


class Refused(RuntimeError):
    pass


# ---------------------------------------------------------------- the only way out
def _allowed(method, path):
    if method == "GET":
        return bool(re.fullmatch(r"/routines\?page=\d+&pageSize=\d+", path) or re.fullmatch(f"/routines/{_ID}", path))
    if method == "PUT":
        return bool(re.fullmatch(f"/routines/{_ID}", path))
    return False

def _request(method, path, key, body=None):
    if not _allowed(method, path):
        raise Refused(f"{method} {path}: only reading routines and updating one are allowed")
    req = urllib.request.Request(
        f"{hevy.API}{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"api-key": key, "accept": "application/json", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def list_routines(key):
    out, page = [], 1
    while True:
        body = _request("GET", f"/routines?page={page}&pageSize=10", key)
        out += body.get("routines", [])
        if page >= body.get("page_count", 1):
            return out
        page += 1

def get_routine(rid, key):
    return _request("GET", f"/routines/{rid}", key)["routine"]

def put_routine(rid, body, key):
    return _request("PUT", f"/routines/{rid}", key, body)


# ---------------------------------------------------------------- pure
def put_body(rt):
    """A routine as read -> the update that writes it back unchanged."""
    def take(d, keep, read_only, what):
        extra = set(d) - keep - read_only
        if extra:
            raise Refused(f"{what} has fields this code doesn't know ({', '.join(sorted(extra))}); "
                          "an update would drop them")
        return {k: d[k] for k in keep if k in d}
    out = take(rt, ROUTINE_KEYS - {"exercises"}, ROUTINE_READ_ONLY | {"exercises"}, "routine")
    out["exercises"] = []
    for e in rt["exercises"]:
        x = take(e, EXERCISE_KEYS - {"sets"}, EXERCISE_READ_ONLY | {"sets"}, f"exercise {e.get('title')}")
        x["sets"] = [take(s, SET_KEYS, SET_READ_ONLY, f"a set of {e.get('title')}") for s in e["sets"]]
        out["exercises"].append(x)
    return {"routine": out}

def lb_to_kg(lb):
    return lb / hevy.KG_TO_LB

def _lb(s):
    return hevy.kg_to_lb(s.get("weight_kg") or 0)

def _set_text(s):
    w, r = _lb(s), s.get("reps")
    rr = s.get("rep_range")
    reps = f"{rr['start']}–{rr['end']}" if rr and rr.get("end") != rr.get("start") else (
        str(rr["start"]) if rr else str(r) if r is not None else "?")
    return f"{w:g} × {reps}" if w else reps

def _with_note(notes, line):
    kept = [x for x in (notes or "").splitlines() if not x.startswith((NOTE, *OLD_NOTES))]
    return "\n".join([line, *kept]) if line else "\n".join(kept)

def restored(rt, before):
    """The routine with each exercise's sets put back from `before` (the routine as it was
    before a deload), matched by exercise template in order. After a deload, Hevy's own
    "update routine" prompt saves the light sets, and only the top set is rewritten after
    that, so ramp and back-off sets would stay light without this."""
    rt = copy.deepcopy(rt)
    by_tpl = {}
    for e in before["exercises"]:
        by_tpl.setdefault(e["exercise_template_id"], []).append(e)
    for e in rt["exercises"]:
        old = by_tpl.get(e["exercise_template_id"])
        if old:
            e["sets"] = copy.deepcopy(old.pop(0)["sets"])
    return rt

def changes_for(rt, plan, verdict, canon, active_correction=lambda name: False):
    """The routine as it should be after today's write. -> (routine, changes, skipped)
    canon: Hevy title -> EsteCoach name. changes: per exercise, what moves, for the preview
    and the log. skipped: (name, why) for what is left alone."""
    rt = copy.deepcopy(rt)
    names = [canon(e["title"]) for e in rt["exercises"]]
    planned = [i for i in plan["exercises"] if not i.get("skipped")]
    changes, skipped = [], []
    for e_name in sorted(set(names) - {i["exercise"] for i in plan["exercises"]}):
        skipped.append((e_name, "not in today's plan"))
    for item in plan["exercises"]:
        if item.get("skipped"):
            skipped.append((item["exercise"], "marked skip in the check-in"))
    factor = plan.get("deload")
    for item in planned:
        at = [k for k, n in enumerate(names) if n == item["exercise"]]
        if not at:
            skipped.append((item["exercise"], "not in this routine"))
            continue
        if active_correction(item["exercise"]):
            skipped.append((item["exercise"], "a correction rule is active for it in Hevy"))
            continue
        line = f"{NOTE} {VERDICT_TEXT.get(verdict, verdict)} · {item['target']}"
        goal = item.get("goal") or {}
        # An exercise listed twice (pull-ups in two supersets) gets the note on both and the
        # target on the occurrence with the heavier sets.
        primary = max(at, key=lambda j: max([s.get("weight_kg") or 0 for s in rt["exercises"][j]["sets"]] or [0]))
        for k in at:
            ex = rt["exercises"][k]
            was = [_set_text(s) for s in ex["sets"]]
            ex["notes"] = _with_note(ex.get("notes"), line)
            what = None
            if factor:
                n = max(1, int(len(ex["sets"]) * factor + 0.5))
                ex["sets"] = ex["sets"][:n]
                for s in ex["sets"]:
                    if s.get("weight_kg"):
                        s["weight_kg"] = lb_to_kg(core.round5(_lb(s) * factor))
                what = "deload"
            elif "weight_lb" in goal and goal["weight_lb"] and core.rep_unit(item["exercise"]) == "":
                normal = [s for s in ex["sets"] if s.get("type") == "normal" and s.get("weight_kg")]
                if not normal:
                    continue
                top = max(s["weight_kg"] for s in normal)
                if k == primary:
                    lo, hi = item.get("reps_range") or (goal["reps"], goal["reps"])
                    tops = [s for s in normal if s["weight_kg"] == top]
                    # Same weight, one more rep (an accessory's "add a rep") is for the weakest
                    # top set only: on straight sets 40 x 10, 9, 8 it is the 8 that becomes 9,
                    # not all three. A new weight goes on every top set.
                    if abs(hevy.kg_to_lb(top) - goal["weight_lb"]) < 0.05 and not item.get("reps_range"):
                        tops = [min(reversed(tops), key=lambda s: s.get("reps") or 0)]
                    # A lighter target (easing back in after easy days) is for every heavy
                    # set, not only the old top ones: a 270 x 6 under 300 x 2, 3 stayed at 270
                    # when the 300s went to 270 x 4-5, and the session was lifted as three
                    # sets at the target (2 Nov 2026). Heavy is HEAVY of the old top weight or
                    # more; ramp and light back-off sets keep their weights.
                    if goal["weight_lb"] < hevy.kg_to_lb(top) - 0.05:
                        tops = [s for s in normal if s["weight_kg"] >= HEAVY * top]
                    for s in tops:
                        s["weight_kg"] = lb_to_kg(goal["weight_lb"])
                        s["reps"] = None
                        s["rep_range"] = {"start": lo, "end": hi}
                    what = "top set"
            now = [_set_text(s) for s in ex["sets"]]
            changes.append({"exercise": item["exercise"], "title": ex["title"], "what": what or "note",
                            "before": was, "after": now, "note": line})
    return rt, changes, skipped

def preview(routine_title, changes, skipped):
    """One line for the button: what the write will change."""
    moved = []
    for c in changes:
        if c["what"] == "top set":
            b = [x for x, y in zip(c["before"], c["after"]) if x != y]
            a = [y for x, y in zip(c["before"], c["after"]) if x != y]
            if a:
                n = f"{len(a)} sets " if len(a) > 1 and len(set(a)) == 1 else ""
                moved.append(f"{c['exercise']} top set {b[-1]} → {n}{a[-1]}")
        elif c["what"] == "deload":
            moved.append(f"{c['exercise']} → {', '.join(c['after'])}")
    notes = len([c for c in changes if c["what"] == "note"])
    tail = []
    if notes:
        tail.append(f"{notes} note{'s' if notes > 1 else ''} only")
    if skipped:
        tail.append(f"left alone: {', '.join(f'{n} ({why})' for n, why in skipped)}")
    head = f"Will change {routine_title}: " + ("; ".join(moved) if moved else "notes only")
    return head + (". " + ". ".join(tail) if tail else "") + "."


# ---------------------------------------------------------------- config and log
def routine_id(cfg, plan):
    return ((cfg.training.get("hevy") or {}).get("routines") or {}).get(f"{plan['kind']} {plan['variant']}")

def enabled(cfg):
    """Only a config that names its routines writes; the demo never does."""
    return bool((cfg.training.get("hevy") or {}).get("routines")) and not cfg.is_demo

def load_log(cfg):
    p = cfg.data_file(LOG)
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))

def _append_log(cfg, row):
    p = cfg.data_file(LOG)
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)

def _pending_restore(cfg, rid):
    """The routine as it was before its last write, if that write was a deload."""
    rows = [r for r in load_log(cfg) if r["routine_id"] == rid and r["source"] != "restore"]
    if rows and rows[-1]["deload"]:
        return json.loads(rows[-1]["before"])
    return None

def _canon(cfg, day):
    names = hevy.load_names(cfg.data_file("hevy_names.csv"))
    renames = load_renames(str(cfg.data_file("renames.csv")))
    return lambda title: rename(renames, day, names.get(title, title), "hevy")

def _corrected(cfg, day):
    rules = load_corrections(str(cfg.data_file("corrections.csv")))
    return lambda name: any(src == "hevy" and ex == name and a <= day <= b
                            for a, b, ex, *_, src in rules)

_cache = {}

def plan_write(cfg, plan, verdict, key, day=None, fresh=False):
    """-> (routine id, base, routine to write, changes, skipped), or None when this config has
    no routine for the day. base is the routine as read, with the sets from before a deload
    put back when the last write was one; it is what the log keeps as "before". Reads are cached ten minutes for the page's preview."""
    day = day or date.today().isoformat()
    rid = routine_id(cfg, plan)
    if not rid:
        return None
    hit = _cache.get(rid)
    if fresh or not hit or time.time() - hit[0] > 600:
        hit = _cache[rid] = (time.time(), get_routine(rid, key))
    pending = _pending_restore(cfg, rid)
    base = restored(hit[1], pending) if pending else hit[1]
    new, changes, skipped = changes_for(base, plan, verdict, _canon(cfg, day), _corrected(cfg, day))
    return rid, base, new, changes, skipped

def send(cfg, plan, verdict, key, source, day=None):
    """Write today's plan to its routine and log it. -> the preview line of what was written."""
    day = day or date.today().isoformat()
    got = plan_write(cfg, plan, verdict, key, day, fresh=True)
    if got is None:
        raise Refused(f"no Hevy routine set for {plan['kind']} {plan['variant']} in [training.hevy.routines]")
    rid, rt, new, changes, skipped = got
    put_routine(rid, put_body(new), key)
    _cache.pop(rid, None)
    _append_log(cfg, {"date": day, "written_at": datetime.now().isoformat(timespec="seconds"),
                      "source": source, "routine_id": rid, "routine": rt["title"],
                      "plan": f"{plan['kind']} {plan['variant']}", "verdict": verdict,
                      "deload": plan.get("deload") or "",
                      "changes": json.dumps(changes, ensure_ascii=False),
                      "before": json.dumps(rt, ensure_ascii=False)})
    return preview(rt["title"], changes, skipped)

def auto(cfg, plan, verdict, key, day=None):
    """Right after the day's call is recorded: write once, never on a rest day."""
    day = day or date.today().isoformat()
    if not plan or verdict == "rest" or not enabled(cfg) or not routine_id(cfg, plan):
        return None
    if any(r["date"] == day and r["source"] == "auto" for r in load_log(cfg)):
        return None
    return send(cfg, plan, verdict, key, "auto", day)

def last_write(cfg, day=None):
    day = day or date.today().isoformat()
    rows = [r for r in load_log(cfg) if r["date"] == day and r["source"] in ("auto", "button", "resave")]
    return rows[-1] if rows else None


# ---------------------------------------------------------------- command line
def _today_plan(cfg):
    from estecoach.analysis import engine
    from estecoach.web import db
    con = db.connect()
    day = date.today().isoformat()
    r = engine.readiness(con, day, cfg.goals)
    return engine.training_plan(con, day, r["verdict"], r["checkin"]), r["verdict"]

def _diff(a, b, path=""):
    if type(a) is not type(b):
        return [f"{path}: {a!r} -> {b!r}"]
    if isinstance(a, dict):
        return [x for k in sorted(set(a) | set(b)) for x in _diff(a.get(k), b.get(k), f"{path}.{k}")]
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items -> {len(b)}"]
        return [x for i, (p, q) in enumerate(zip(a, b)) for x in _diff(p, q, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} -> {b!r}"]

def main(argv):
    from estecoach import config
    cfg = config.load()
    key = hevy.api_key(cfg)
    if "--link" in argv:
        print("[training.hevy.routines]")
        seen = set()
        for rt in sorted(list_routines(key), key=lambda r: (len(r["title"]), r["title"])):
            m = re.match(r"(push|pull|legs)\s+([AB])\b", rt["title"], re.I)
            label = f'"{m[1].lower()} {m[2].upper()}"' if m else None
            if label is None or label in seen:      # a spare copy or an unrelated routine
                print(f'# {label or "?"} = "{rt["id"]}"   # {rt["title"]}')
                continue
            seen.add(label)
            print(f'{label} = "{rt["id"]}"   # {rt["title"]}')
        return 0
    if "--check" in argv:
        rid = argv[argv.index("--check") + 1]
        a = get_routine(rid, key)
        put_routine(rid, put_body(a), key)
        b = get_routine(rid, key)
        diff = [d for d in _diff(a, b) if not d.startswith(".updated_at")]
        print(f"{a['title']}: " + ("unchanged after an unchanged update" if not diff else "CHANGED:\n  " + "\n  ".join(diff)))
        print("Rep ranges and exercise notes come back in the read, so they are in the diff. A note on")
        print("the routine itself never does: check that one in the app.")
        return 1 if diff else 0
    if "--restore" in argv:
        day = argv[argv.index("--restore") + 1]
        rows = [r for r in load_log(cfg) if r["date"] == day and r["source"] in ("auto", "button", "resave")]
        if not rows:
            print(f"no write logged on {day}")
            return 1
        before = json.loads(rows[0]["before"])
        put_routine(rows[0]["routine_id"], put_body(before), key)
        _append_log(cfg, {**rows[0], "written_at": datetime.now().isoformat(timespec="seconds"),
                          "source": "restore", "changes": "[]", "deload": ""})
        print(f"{before['title']}: put back as it was before {day}'s write")
        return 0
    plan, verdict = _today_plan(cfg)
    if not plan:
        print("no plan today")
        return 0
    if "--send" in argv:
        print(send(cfg, plan, verdict, key, "cli"))
        return 0
    got = plan_write(cfg, plan, verdict, key, fresh=True)
    print(preview(got[1]["title"], got[3], got[4]) if got else f"no routine set for {plan['kind']} {plan['variant']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

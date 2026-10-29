#!/usr/bin/env python3
"""Hevy API -> raw snapshot, and snapshot -> sets for the lifting tables. Stdlib only.

Two halves, kept apart on purpose:

  fetch      network. Pulls every workout and writes data_dir/raw/hevy/workouts.json.
             Run as `python -m estebot.parsers.hevy`. Read-only: the API can also create
             and edit workouts and routines, and nothing here ever calls those endpoints.
  hevy_sets  pure. Snapshot -> set rows. Skips the Jefit history hevy_import.py posted. parsers/jefit.py calls it, so every lifting table
             is still built from one set list by one implementation of volume and e1RM.

A full pull every sync rather than an incremental one: at a few hundred workouts a year it
costs nothing, and it picks up edits and deletions made in the app with no extra logic.

On any date that has a Hevy workout, Hevy replaces Jefit. Logging the same sessions in both
apps showed the Jefit copy was routine names and pre-filled values, not what was done.
"""
import csv, json, os, subprocess, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.hevyapp.com/v1"
PAGE_SIZE = 10                      # the API's maximum for /workouts
KG_TO_LB = 2.20462262185            # exact by definition (1 lb = 0.45359237 kg)
# Workouts posted by hevy_import.py carry this at the start of their description. They are
# Jefit history, already in the tidy tables from Jefit's own export, so they are skipped
# here; reading them back would replace every Jefit day with a copy under Hevy's names.
IMPORT_TAG = "Imported from Jefit by EsteBot"


# ---------------------------------------------------------------- key
def api_key(cfg):
    key = os.environ.get("HEVY_API_KEY")
    if key:
        return key.strip()
    src = cfg.sources.get("hevy", {})
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-w",
             "-s", src.get("keychain_service", "estebot-hevy"),
             "-a", src.get("keychain_account", "api-key")],
            capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit("No Hevy API key: set $HEVY_API_KEY or add it to the Keychain:\n"
                         "  security add-generic-password -U -s estebot-hevy -a api-key -w <key>"
                         ) from e
    return out.stdout.strip()


# ---------------------------------------------------------------- fetch
def _get(path, key):
    req = urllib.request.Request(f"{API}{path}", headers={"api-key": key, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def fetch_workouts(key):
    out, page = [], 1
    while True:
        body = _get(f"/workouts?page={page}&pageSize={PAGE_SIZE}", key)
        out.extend(body.get("workouts", []))
        if page >= body.get("page_count", 0):
            break
        page += 1
    return sorted(out, key=lambda w: w["start_time"])

def snapshot_path(cfg):
    return cfg.data_file("raw") / "hevy" / "workouts.json"


# ---------------------------------------------------------------- names
def load_names(path):
    """hevy_names.csv: hevy_title -> canonical exercise key (the name the history uses)."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {r["hevy_title"]: r["exercise"] for r in csv.DictReader(f)}


# ---------------------------------------------------------------- convert (pure)
def local_date(iso, tz=None):
    """The API returns UTC. A workout's day is the local day it happened on; an evening
    session would otherwise land on tomorrow. tz=None means the machine's zone."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(tz).date().isoformat()

def kg_to_lb(kg):
    # Hevy stores whatever was typed converted to kg as a full float, so this recovers the
    # typed pound value exactly at one decimal: 61.2350... kg -> 135.0, 31.0710... -> 68.5.
    return round(kg * KG_TO_LB, 1) if kg else 0.0

def _reps(s):
    """A carry is weight + distance in Hevy, with no reps. Jefit's carries put a rough
    distance in the reps column, and the history import posted that number as metres, so
    metres go in reps here too and the carry series stays in one column. Without this every
    Hevy carry set had 0 reps and was dropped as a missed rep."""
    if not s.get("reps") and s.get("distance_meters"):
        # reps arrives as None, or as 0 once the workout is edited on the phone (17 Oct 2026:
        # all three carry sets were dropped that way)
        return int(s["distance_meters"])
    return s.get("reps") or 0

def is_imported(w):
    return (w.get("description") or "").startswith(IMPORT_TAG)

def hevy_sets(workouts, names, tz=None):
    """-> (rows, unmapped_titles, session_minutes_by_date).

    Rows carry date, exercise, set_idx, weight_lb, reps, rpe, set_type. Volume, e1RM and
    muscle group are deliberately NOT computed here; jefit.py computes them for every set.
    The same exercise can appear twice in one workout (pull-ups straight and in a superset),
    so set_idx counts per exercise per day across blocks, as Jefit's does.
    An unmapped title passes through unchanged, so it shows up as its own exercise rather
    than being guessed into somebody else's history."""
    rows, unmapped, minutes = [], set(), defaultdict(float)
    idx = defaultdict(int)
    for w in sorted(workouts, key=lambda w: w["start_time"]):
        if is_imported(w):
            continue
        day = local_date(w["start_time"], tz)
        if w.get("end_time"):
            a = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(w["end_time"].replace("Z", "+00:00"))
            minutes[day] += (b - a).total_seconds() / 60
        for ex in sorted(w.get("exercises", []), key=lambda e: e["index"]):
            title = ex["title"]
            name = names.get(title)
            if name is None:
                unmapped.add(title); name = title
            for s in sorted(ex.get("sets", []), key=lambda s: s["index"]):
                idx[(day, name)] += 1
                rows.append({
                    "date": day, "exercise": name, "set_idx": idx[(day, name)],
                    "weight_lb": kg_to_lb(s.get("weight_kg")),
                    "reps": _reps(s),
                    "rpe": s.get("rpe"), "set_type": s.get("type") or "normal",
                })
    return rows, unmapped, dict(minutes)

def load_snapshot(cfg):
    """The pulled workouts. With Hevy configured and no snapshot yet, this stops instead of
    returning nothing: the lifting tables would otherwise be rebuilt from Jefit alone and
    lose every session since the switch. It happened once, on a new server whose first sync
    was a phone upload, before any Hevy pull; the truncated tables were committed and pushed."""
    p = snapshot_path(cfg)
    if not p.exists():
        if "hevy" in cfg.sources:
            raise SystemExit(f"No Hevy snapshot at {p}: run `python -m estebot.parsers.hevy` first.")
        return []
    return json.loads(p.read_text())["workouts"]


# ---------------------------------------------------------------- main
def main():
    from estebot import config
    cfg = config.load()
    path = snapshot_path(cfg)
    before = {w["id"]: w["updated_at"] for w in load_snapshot(cfg)}

    workouts = fetch_workouts(api_key(cfg))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workouts": workouts}, indent=1, sort_keys=True))

    own = [w for w in workouts if not is_imported(w)]
    new = [w for w in own if w["id"] not in before]
    changed = [w for w in own if w["id"] in before and before[w["id"]] != w["updated_at"]]
    gone = set(before) - {w["id"] for w in workouts}
    print(f"hevy: {len(own)} workouts -> {path}"
          + (f" (plus {len(workouts) - len(own)} imported from Jefit, ignored)" if len(own) < len(workouts) else ""))
    for label, ws in (("new", new), ("edited in the app", changed)):
        for w in ws:
            print(f"   {label}: {local_date(w['start_time'])} {w['title']}")
    if gone:
        print(f"   ! {len(gone)} workout(s) deleted in the app since the last pull")

    names = load_names(cfg.data_file("hevy_names.csv"))
    _, unmapped, _ = hevy_sets(workouts, names)
    if unmapped:
        print(f"   ! {len(unmapped)} Hevy exercise(s) not in hevy_names.csv - kept under their"
              " Hevy title until mapped:")
        for t in sorted(unmapped):
            print(f"     {t}")


if __name__ == "__main__":
    main()

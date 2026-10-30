"""Each morning's readiness call, kept as it was made, to be graded against the session after.

The page recomputes the call on every load, so it drifts as data arrives and as the rules
change; a call recomputed later would grade today's code on old data. The first call made
each day is written to calls.csv in the data repo instead, one row per day, and never
rewritten. The sync commits it with the tidy tables.

    PYTHONPATH=src python -m estebot.analysis.calls            # record today's call, once
    PYTHONPATH=src python -m estebot.analysis.calls --report   # each recorded call, then how the session went
"""
import csv, json, subprocess, sys
from datetime import date, datetime, timedelta
from pathlib import Path

from estebot import config
from estebot.analysis import engine
from estebot.web import db

AREAS = ("load", "effort", "sleep", "recovery", "feel", "nutrition", "pace")
FIELDS = ("date", "recorded_at", "code", "verdict", "score", *AREAS, "acwr", "plan", "targets")
# Before noon a call waits for last night's sleep and this morning's check-in: the first call
# is the one kept, so a phone upload at 6 a.m. would otherwise record the day's call without
# the check-in made at 7. Saving a check-in records the call (web/main.py), so a morning
# check-in is in it; from noon the call is recorded with whatever there is.
WAIT_UNTIL = 12


def waiting_for(con, now):
    """What the morning's call still waits for, in words; [] when it can be recorded."""
    if now.hour >= WAIT_UNTIL:
        return []
    day, night = now.date().isoformat(), (now.date() - timedelta(days=1)).isoformat()
    missing = []
    if not con.execute("SELECT 1 FROM sleep WHERE night=?", (night,)).fetchone():
        missing.append("last night's sleep")
    if not con.execute("SELECT 1 FROM checkin WHERE date=?", (day,)).fetchone():
        missing.append("this morning's check-in")
    return missing


def _code_version():
    """The commit the call was made by, so a later rule change can be told apart; '' when the
    code isn't a git checkout (the server runs from a clean export)."""
    r = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def make_call(con, day, goals, training=None):
    """Today's call as a calls.csv row: the verdict, every area's level and the plan's targets."""
    r = engine.readiness(con, day, goals)
    plan = engine.training_plan(con, day, r["verdict"], r["checkin"], training=training)
    levels = {x["area"]: x["level"] or "" for x in r["signals"]}
    targets = [{"exercise": e["exercise"], "action": e["action"], "target": e["target"], "goal": e["goal"]}
               for e in (plan or {}).get("exercises", [])]
    return {"date": day, "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "code": _code_version(), "verdict": r["verdict"], "score": r["score"],
            **{a: levels.get(a, "") for a in AREAS},
            "acwr": "" if r["acwr"] is None else f"{r['acwr']:.3f}",
            "plan": " ".join(x for x in ((plan or {}).get("kind"), (plan or {}).get("variant")) if x),
            "targets": json.dumps(targets, separators=(",", ":"), ensure_ascii=False)}


def load(path):
    if not Path(path).exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def record(path, row):
    """Append the row unless its day is already there. -> True if written."""
    if any(c["date"] == row["date"] for c in load(path)):
        return False
    new = not Path(path).exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return True


def graded(con, rows):
    """{verdict: {"n", "hit"}} over the recorded calls whose day had a session: the
    self-grade on calls as they were made, not as today's rules would make them."""
    out = {}
    for c in rows:
        o = engine.session_outcome(con, c["date"])
        if o is not None:
            out.setdefault(c["verdict"], []).append(o["hit"])
    return {v: {"n": len(h), "hit": sum(h) / len(h)} for v, h in out.items()}


def main(argv):
    cfg = config.load()
    path = cfg.data_file("calls.csv")
    con = db.connect()
    if "--report" in argv:
        for c in load(path):
            o = engine.session_outcome(con, c["date"])
            got = "no session" if o is None else (
                f"{sum(x['met'] for x in o['exercises'])}/{len(o['exercises'])} targets met ({o['hit']:.0%})")
            print(f"{c['date']}  {c['verdict']:6} {c['score']:>3}  {c['plan']:10}  {got}")
        return 0
    now = datetime.now()
    day = now.date().isoformat()
    if missing := waiting_for(con, now):
        print(f"{day}: waiting for {' and '.join(missing)} before recording the call")
        return 0
    row = make_call(con, day, cfg.goals)
    print(f"{day}: {row['verdict']} {row['score']}"
          + (" recorded" if record(path, row) else ", already recorded today; kept the first"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

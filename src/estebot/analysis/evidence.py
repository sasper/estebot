"""The evidence table: each readiness area's track record against the sessions that followed.

    PYTHONPATH=src python -m estebot.analysis.evidence [START [END]]   # default: 2022 to today

Slow on a long history (every session's morning call is recomputed), so it is a report, not
something the page computes on each load.
"""
import sys
from datetime import date

from estebot import config
from estebot.analysis import engine
from estebot.web import db

VERDICT = {"hard": "push", "normal": "as planned", "light": "hold", "rest": "rest"}


def pts(x):
    return f"{x * 100:+.1f}"


def main(argv):
    start = argv[0] if argv else "2022-01-01"
    end = argv[1] if len(argv) > 1 else date.today().isoformat()
    ev = engine.evidence(db.connect(), start, end, config.load().goals)
    print(f"{ev['sessions']} sessions graded, {start} to {end}. Each session's share of targets met is")
    print(f"compared with the sessions within {engine.PHASE_DAYS} days of it; points are percentage points.\n")
    print(f"{'area':10} {'flagged':>7} {'clear':>6}   {'flagged vs clear':>16}   {'95% band':>15}   verdict")
    for area, e in ev["areas"].items():
        band = "" if e["diff"] is None else f"{pts(e['lo'])} to {pts(e['hi'])}"
        diff = "" if e["diff"] is None else pts(e["diff"])
        print(f"{area:10} {e['n_flagged']:7} {e['n_clear']:6}   {diff:>16}   {band:>15}   "
              f"{e['status']}{' -> votes' if e['votes'] else ''}")
    print("\nreasons to push (they count only once days with them went better):")
    for name, e in ev["push_reasons"].items():
        band = "" if e["diff"] is None else f"{pts(e['diff'])} ({pts(e['lo'])} to {pts(e['hi'])})"
        print(f"  {name:36} {e['n_flagged']:4} vs {e['n_clear']:4}   {band}   {e['status']}"
              f"{' -> earns a push' if e['supports'] else ''}")
    print("\ncandidates, tracked but not voting until new sessions confirm them:")
    for name, e in ev["candidates"].items():
        band = "" if e["diff"] is None else f"{pts(e['diff'])} ({pts(e['lo'])} to {pts(e['hi'])})"
        print(f"  {name:36} {e['n_flagged']:4} vs {e['n_clear']:4}   {band}   {e['status']}")
    print("\nthe call each session would get under these votes (in-sample, so it flatters the rule):")
    print(f"{'call':13} {'sessions':>8}   {'targets met':>11}   {'vs its phase':>12}")
    for v, g in ev["verdicts"].items():
        print(f"{VERDICT[v]:13} {g['n']:8}   {g['hit']:11.0%}   {pts(g['vs_phase']):>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""The lifting summary tables, built from set rows: lift_sessions, weekly_volume and
exercise_progression.

jefit.py (the real history) and demo.py (the made-up person) both write these, and they
used to build them separately. Here once, so the two layouts and their rounding can't
drift apart. Stdlib only, no I/O.

A set row is a dict with the sets.csv fields: date, exercise, muscle_group, weight_lb,
reps, volume_lb, e1rm_lb, rpe ("" when not logged) and set_type.
"""
from collections import defaultdict
from datetime import date

from estebot.core import is_hard_set

SESSION_COLS = ["date", "n_exercises", "n_sets", "total_volume_lb", "top_set_lb", "muscle_groups",
                "session_min", "jefit_kcal", "hard_sets", "mean_rpe"]
PROGRESSION_COLS = ["exercise", "muscle_group", "date", "best_e1rm_lb", "best_set_weight_lb",
                    "best_set_reps", "day_volume_lb", "day_sets"]


def weekly_volume_cols(groups):
    return (["week"] + [f"{g}_vol" for g in groups] + [f"{g}_sets" for g in groups]
            + ["total_vol", "total_sets"])


def _by_day(sets):
    byday = defaultdict(list)
    for s in sets:
        byday[s["date"]].append(s)
    return byday


def _mean_rpe(ss):
    """Mean RPE of the working sets that have one; blank when none were logged."""
    r = [float(x["rpe"]) for x in ss
         if x["rpe"] not in ("", None) and (x["set_type"] or "").lower() != "warmup"]
    return round(sum(r) / len(r), 2) if r else ""


def session_rows(sets, minutes=None, kcal=None):
    """One lift_sessions.csv row per training day. minutes and kcal map a date to the
    session's length and the logging app's calorie estimate; a day missing from either
    gets a blank."""
    minutes, kcal = minutes or {}, kcal or {}
    byday = _by_day(sets)
    return [[d, len({x["exercise"] for x in ss}), len(ss),
             round(sum(x["volume_lb"] for x in ss)),
             max(x["weight_lb"] for x in ss),
             "|".join(sorted({x["muscle_group"] for x in ss})),
             minutes.get(d, ""), kcal.get(d, ""),
             sum(is_hard_set(x["rpe"], x["set_type"]) for x in ss),
             _mean_rpe(ss)]
            for d, ss in sorted(byday.items())]


def weekly_volume_rows(sets, groups):
    """One weekly_volume.csv row per ISO week: volume and set count per muscle group, in
    the order of `groups`, then the totals."""
    vol, n = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(int))
    for s in sets:
        y, w, _ = date.fromisoformat(s["date"]).isocalendar()
        k = f"{y}-W{w:02d}"
        vol[k][s["muscle_group"]] += s["volume_lb"]
        n[k][s["muscle_group"]] += 1
    return [[k] + [round(vol[k].get(g, 0)) for g in groups] + [n[k].get(g, 0) for g in groups]
            + [round(sum(vol[k].values())), sum(n[k].values())]
            for k in sorted(vol)]


def progression_rows(sets):
    """One exercise_progression.csv row per exercise and day: the best set by e1RM (the
    first one logged on a tie) and the day's volume and set count for that exercise."""
    best, byday = defaultdict(dict), _by_day(sets)
    for s in sets:
        cur = best[s["exercise"]].get(s["date"])
        if not cur or s["e1rm_lb"] > cur["e1rm_lb"]:
            best[s["exercise"]][s["date"]] = s
    out = []
    for ex in sorted(best):
        for d in sorted(best[ex]):
            b = best[ex][d]
            day = [x for x in byday[d] if x["exercise"] == ex]
            out.append([ex, b["muscle_group"], d, b["e1rm_lb"], b["weight_lb"], b["reps"],
                        round(sum(x["volume_lb"] for x in day)), len(day)])
    return out

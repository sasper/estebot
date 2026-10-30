"""Pure functions with no I/O. Everything here is a correctness trap that bit
this project at least once, which is why each one is isolated and tested.
"""
from __future__ import annotations

import math
import re
import statistics as st
from datetime import date, datetime, timedelta

# Sleep sessions span midnight, so a calendar date splits them in half. Shifting
# back 12 h puts a 23:00-07:00 session entirely on the evening's date and sends
# an afternoon nap forward to the coming night.
NIGHT_SHIFT_HOURS = 12


def night_of(start: datetime, shift_hours: int = NIGHT_SHIFT_HOURS) -> date:
    """The night a sleep segment belongs to."""
    return (start - timedelta(hours=shift_hours)).date()


def union_minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total minutes covered by intervals, overlaps merged exactly once.

    Apple Health emits overlapping sleep records from multiple sources (Watch
    and iPhone) and across stage types. Summing durations double-counts; this
    merges first.
    """
    spans = sorted(i for i in intervals if i[1] > i[0])
    if not spans:
        return 0.0
    total = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total / 60.0


def dedupe_daily_total(per_source: dict[str, float]) -> float:
    """One day's total for a metric recorded by several devices.

    Watch and iPhone both record steps over overlapping intervals, so adding
    the sources inflates the day. Health.app interleaves them properly; the
    largest single-source total is the closest approximation available from
    the export.
    """
    return max(per_source.values()) if per_source else 0.0


def epley_e1rm(weight: float, reps: int, max_reps: int = 10) -> float | None:
    """Estimated 1RM. Returns None above `max_reps`, where Epley drifts badly.

    None rather than 0.0 on purpose: a zero is a number and will silently
    corrupt any min(), mean() or median() taken over the column.
    """
    if reps <= 0 or weight <= 0 or reps > max_reps:
        return None
    return weight * (1 + reps / 30.0)


def parse_jefit_logs(logs: str) -> list[tuple[float, int]]:
    """Jefit packs a session into one string: "225x8,225x6,225x5"."""
    out = []
    for token in (logs or "").split(","):
        token = token.strip()
        if "x" not in token:
            continue
        w, _, r = token.partition("x")
        try:
            weight, reps = float(w), float(r)
        except ValueError:
            continue
        if 0 < reps <= 100:
            out.append((weight, int(reps)))
    return out


def drop_subset_duplicates(log_strings: list[str]) -> list[str]:
    """Jefit emits partial duplicates: the same (date, exercise) twice, where
    one row's log string is a substring of the other's. Keep the superset.
    """
    kept: list[str] = []
    for s in sorted((x for x in log_strings if x), key=len, reverse=True):
        if not any(s in k for k in kept):
            kept.append(s)
    return kept


def order_by_name_date(paths: list[str]) -> list[str]:
    """Order exports by the YYYYMMDD in their filename, oldest first.

    The Jefit exports are now an archive in git, and a fresh clone resets every
    file's mtime, so mtime order would become checkout order. The merge is
    oldest-first on purpose (a newer export's version of a day must land last),
    so after a restore the order has to come from the name. Ties and undated
    names keep their incoming order.
    """
    import os, re
    def key(ip):
        i, p = ip
        m = re.search(r"(?<!\d)(\d{8})(?!\d)", os.path.basename(p))
        return (m.group(1) if m else "", i)
    return [p for _, p in sorted(enumerate(paths), key=key)]


def ols_slope_per_week(points: list[tuple[date, float]]) -> float | None:
    """Least-squares trend of (date, value) points, in units per week (positive = rising).

    x is days since the first point, not the point's index: weigh-ins are irregular, and
    treating index as time would turn a two-week gap into a single day. None below three
    points or when every point falls on one day, where there is no slope to fit.
    """
    if len(points) < 3:
        return None
    x0 = points[0][0]
    xs = [(p[0] - x0).days for p in points]; ys = [p[1] for p in points]
    mx, my = st.mean(xs), st.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) * 7


def rolling(points: list[tuple[date, float]], window: int = 14) -> list[tuple[date, float]]:
    """Trailing mean over the last `window` calendar days, one output per input point.

    A calendar window, not the last N points: after a week of missed weigh-ins the trend
    must not reach back across the gap as if those days were logged.
    """
    out = []
    for dt, _ in points:
        m = trailing_mean(points, dt, window)
        if m is not None:
            out.append((dt, m))
    return out


def trailing_mean(points: list[tuple[date, float]], day: date, window: int = 14) -> float | None:
    """Mean of the points in the `window` calendar days ending on `day`, or None if none.

    The 14-day weight trend on any date, weigh-in or not. Pull-ups are logged at 0 lb, so
    their load is body weight, and a single morning's reading swings 2-3 lb; the trend is
    the same number the app's trend line shows that day. None, not a guess, when no
    weigh-in falls in the window: gaps of two months between weigh-ins really happen.
    """
    w = [v for (x, v) in points if 0 <= (day - x).days < window]
    return st.mean(w) if w else None


def weeks_to_goal(current: float, rate_per_week: float | None, goal: float) -> float | None:
    """Weeks until `current` reaches `goal` if `rate_per_week` holds. None if the trend is
    not moving toward the goal (flat, the wrong way, or already there).

    A straight line on purpose: it answers "at the current pace", which is the question.
    Weight loss usually slows, so the page labels it as that and nothing more.
    """
    if rate_per_week is None or current == goal:
        return None
    gap = goal - current
    if rate_per_week == 0 or (gap < 0) != (rate_per_week < 0):
        return None
    return gap / rate_per_week


HARD_SET_RPE = 7.0

def is_hard_set(rpe: str | float | None, set_type: str = "") -> bool:
    """A set that counts toward training intensity: not a warm-up, and RPE 7+ where RPE was
    logged. With no RPE (all of the Jefit era, and Hevy sets left blank) every working set
    counts, because "unknown effort" must not silently become "easy"."""
    if (set_type or "").lower() == "warmup":
        return False
    if rpe in (None, ""):
        return True
    return float(rpe) >= HARD_SET_RPE


def pace(rate_per_week: float | None, body_lb: float | None,
         min_pct: float, max_pct: float) -> tuple[str | None, float | None]:
    """Classify a weight change against the planned loss range, as % of body weight a week.

    -> (status, pct_per_week). status is "gaining", "slow" (losing less than expected),
    "on_pace", "fast" (losing faster than planned), or None when there is nothing to judge.
    """
    if rate_per_week is None or not body_lb:
        return None, None
    pct = -rate_per_week / body_lb * 100          # positive = losing
    if pct < 0:
        return "gaining", pct
    if pct < min_pct:
        return "slow", pct
    if pct > max_pct:
        return "fast", pct
    return "on_pace", pct


# Being sick, from a free-text note. Narrow words on purpose: ingest's EXCUSE_HINTS matches
# "ill" inside "will" and "still", and "cold" would catch "cold brew". The calendar's sick
# days and the readiness check-in both read this, so a note cannot count as sick in one
# place and not the other.
SICK = re.compile(r"\bsick\b|\bill\b|\bflu\b|fever|covid|headache|migraine|stomach bug|vomit", re.I)


def checkin_energy(energy: int | None, sick: bool) -> int | None:
    """Energy (0-3) for a check-in. Being sick is the lowest energy there is, so a sick day
    with no energy answer reads 0. Without that, "stomach bug, sick all day" left the feel
    signal empty and the day could still come out as a hard session.

    An energy answer the person gave wins over the sick flag: a cold that leaves you feeling
    fine is yours to call. A note never carries a number, so a sick note always reads 0.
    """
    if energy is None and sick:
        return 0
    return energy


LEVELS = ("green", "amber", "red")

def combine_signals(levels: dict[str, str | None], forced_rest: bool = False) -> str:
    """The readiness verdict from per-area signals: no single input decides on its own.

    levels: area -> "green" | "amber" | "red" | None (no data; counts for nothing).
      rest   red in 2+ areas
      light  red in 1 area, or amber in 2+
      hard   every area green, and the recovery/sleep/load areas actually measured
      normal anything else (at most one amber)
    Replaced a set of single-input rules (6 days in a row -> rest, one bad reading ->
    light) that read as prescriptions rather than an assessment.

    forced_rest: the one deliberate exception, a rule that a back spasm means rest
    whatever else says. It is an injury, not a fatigue signal.
    """
    if forced_rest:
        return "rest"
    vals = [v for v in levels.values() if v]
    reds, ambers = vals.count("red"), vals.count("amber")
    if reds >= 2:
        return "rest"
    if reds == 1 or ambers >= 2:
        return "light"
    if ambers == 0 and all(levels.get(a) == "green" for a in ("recovery", "sleep", "load")):
        return "hard"
    return "normal"


# Each verdict owns a band of the 0-100 readiness score, so the number can never contradict
# the call next to it (six good areas and one strained must not read 86 beside "take it easy").
SCORE_BANDS = {"rest": (0, 24), "light": (25, 49), "normal": (50, 74), "hard": (75, 100)}

def readiness_score(levels: dict[str, str | None], verdict: str) -> int:
    """0-100: the verdict's band, placed within it by how clear the areas are.

    Green counts 1, amber 1/2, red and no data 0, over every area, so a day with missing
    data scores lower than the same day fully measured. The verdict decides; the score
    only says how close to the next band up the day sits."""
    lo, hi = SCORE_BANDS[verdict]
    clear = sum({"green": 1, "amber": 0.5}.get(v, 0) for v in levels.values())
    return lo + round(clear / max(len(levels), 1) * (hi - lo))


def weekly_shift_z(week: list[float], baseline: list[float]) -> float | None:
    """How unusual this week's mean is against a baseline of single days, in standard
    errors of a mean of len(week) readings. The recovery signal divided by the spread of
    single nights, which a week's mean almost never reaches: HRV never got past -0.6
    against an amber line at -1.0 in 90 days, so recovery read green every day. The
    baseline must not include the week it is compared with."""
    if len(week) < 3 or len(baseline) < 5:
        return None
    sd = st.pstdev(baseline)
    if sd == 0:
        return None
    return (st.mean(week) - st.mean(baseline)) / (sd / math.sqrt(len(week)))


def worst(*levels: str | None) -> str | None:
    """The most severe of several levels; None only if all are None."""
    known = [l for l in levels if l]
    return max(known, key=LEVELS.index) if known else None


def monotony(daily_loads: list[float]) -> float | None:
    """Foster's training monotony: mean daily load / its standard deviation over a week.
    High when every day looks alike (no lighter days), which is what a long run of training
    days without rest really costs; it measures that instead of counting days."""
    if len(daily_loads) < 7:
        return None
    mean, sd = st.mean(daily_loads), st.pstdev(daily_loads)
    if sd == 0:
        # seven identical days: the most monotonous week there is, not an unmeasured one.
        # Seven zero days is no training at all, which has no monotony.
        return math.inf if mean > 0 else None
    return mean / sd


# ---------------------------------------------------------------- strength standards
# Common male 1RM / bodyweight bands (novice, intermediate, advanced, elite), quoted for a
# lifter of about REF_BODY_LB. Plain ratios understate heavy lifters: strength grows with
# roughly bodyweight^(2/3), not bodyweight, so at 260 lb a raw 1.8x squat reads
# "intermediate" while 470 lb is an advanced squat at that size. Thresholds are scaled
# allometrically from the reference instead. The bands are reference data, not measurements.
STRENGTH_BANDS = {
    "squat": (1.0, 1.5, 2.0, 2.5),
    "bench": (0.75, 1.0, 1.5, 2.0),
    "deadlift": (1.25, 1.75, 2.5, 3.0),
    "press": (0.5, 0.75, 1.0, 1.25),
}
STRENGTH_LEVELS = ("beginner", "novice", "intermediate", "advanced", "elite")
REF_BODY_LB = 180.0


def strength_thresholds(lift: str, body_lb: float) -> list[float]:
    """1RM in lb that marks novice, intermediate, advanced and elite at `body_lb`."""
    scale = REF_BODY_LB * (body_lb / REF_BODY_LB) ** (2 / 3)
    return [band * scale for band in STRENGTH_BANDS[lift]]


def strength_level(lift: str, one_rm_lb: float, body_lb: float) -> tuple[str, float | None]:
    """-> (level, 1RM needed for the next level, or None at elite)."""
    th = strength_thresholds(lift, body_lb)
    n = sum(one_rm_lb >= t for t in th)
    return STRENGTH_LEVELS[n], (th[n] if n < len(th) else None)


# DOTS (men): the IPF-era formula for comparing powerlifting totals across bodyweights.
DOTS_MALE = (-307.75076, 24.0900756, -0.1918759221, 0.0007391293, -0.000001093)
LB_PER_KG = 2.20462262185


def dots(total_lb: float, body_lb: float) -> float:
    """DOTS score of a squat + bench + deadlift total. Bodyweight is clamped to the
    formula's fitted range (40-210 kg) as the federations do."""
    bw = min(max(body_lb / LB_PER_KG, 40.0), 210.0)
    a, b, c, d, e = DOTS_MALE
    return total_lb / LB_PER_KG * 500 / (a + b * bw + c * bw ** 2 + d * bw ** 3 + e * bw ** 4)


# ---------------------------------------------------------------- progression
# One rule for "what next time", applied to the last time an exercise was done. Written down
# from advice that had been given by hand (a triple at RPE 10 on the press: hold the load and
# make it cleaner; a set of 6 at RPE 9 on dumbbell bench: add a rep), so the page gives the
# same answer every time instead of whatever was said that day.
STRENGTH_REPS = 5      # a top set of 5 or fewer is strength work: progress by load
HOLD_RPE = 9.5         # a grinder: repeat the load until it moves at RPE 9
ADD_LOAD_RPE = 8.0     # strength work at or under this adds load; above it adds a rep
# Defaults for the rep ranges; a person's own come from the config's [training] section.
REP_RANGE = (8, 12)
HIGH_REP_RANGE = (12, 15)
HIGH_REP = ("calf", "lateral raise", "shrug", "face pull", "leg extension", "fly")
LOWER_BODY = ("squat", "deadlift", "lunge", "clean", "romanian", "stiff", "hip thrust",
              "glute bridge", "leg press", "calf")


def load_step(exercise: str) -> float:
    """The smallest sensible jump in lb. Dumbbells are logged per hand, so 5 is per hand."""
    e = exercise.lower()
    if "dumbbell" in e:
        return 5.0
    if "carry" in e or "farmer" in e:
        return 20.0
    if any(k in e for k in LOWER_BODY):
        return 10.0
    if "machine" in e:
        return 10.0
    return 5.0


def _fmt(w: float) -> str:
    return f"{w:g}"


def rep_unit(exercise: str) -> str:
    """Carries log distance in the reps column: 225 x 45 is 45 yards."""
    e = exercise.lower()
    return " yd" if ("carry" in e or "farmer" in e) else ""


def progression(exercise: str, sets: list[tuple[float | None, int, float | None]],
                high_rep=HIGH_REP, rep_range=REP_RANGE, high_rep_range=HIGH_REP_RANGE) -> dict | None:
    """Next target from the last session's sets of one exercise: (weight_lb, reps, rpe).

      bodyweight (all sets at 0 lb)  beat the last total reps by 1-2; hold if any set was 9.5+
      no RPE on the top sets         repeat: unknown effort is not evidence it was easy
      any top set at RPE 9.5+        hold the load, same reps, aim for RPE 9
      strength (top set <= 5 reps)   from the best set at that weight: RPE <= 8 adds load,
                                     8.5-9 adds a rep (315 x 1 then 315 x 3 is a triple)
      accessory                      add a rep to the weakest top set up to the top of its
                                     range, rep_range (8-12 unless configured; high_rep_range,
                                     12-15, for high_rep work), then add load and drop
                                     to the bottom of it. Work done above its range
                                     on purpose (60 x 20 rows) adds load and keeps the reps.
    The top sets are those at the heaviest weight. -> {action, target, why} or None."""
    work = [(w or 0.0, r, rpe) for w, r, rpe in sets if r]
    if not work:
        return None
    top_w = max(w for w, _, _ in work)
    if top_w == 0:
        total, n = sum(r for _, r, _ in work), len(work)
        rpes = [x for *_, x in work if x is not None]
        why = f"last time {n} sets, {total} reps" + (f", hardest RPE {max(rpes):g}" if rpes else "")
        if rpes and max(rpes) >= HOLD_RPE:
            return {"action": "hold", "target": f"{n} sets, {total} reps again, cleaner", "why": why}
        return {"action": "add reps", "target": f"{n} sets, {total + 1}–{total + 2} reps in total",
                "why": why}
    top = [(r, rpe) for w, r, rpe in work if w == top_w]
    strength = min(r for r, _ in top) <= STRENGTH_REPS
    reps = max(r for r, _ in top) if strength else min(r for r, _ in top)
    rpes = [x for _, x in top if x is not None]
    hardest = max(rpes) if rpes else None
    w, step, u = _fmt(top_w), load_step(exercise), rep_unit(exercise)
    why = f"top set {w} × {reps}{u}" + (f" at RPE {hardest:g}" if hardest is not None else ", no RPE logged")
    if hardest is None:
        return {"action": "repeat", "target": f"{w} × {reps}{u}, and log the RPE", "why": why}
    if hardest >= HOLD_RPE:
        return {"action": "hold", "target": f"{w} × {reps}{u} at RPE 9", "why": why + ": make it cleaner first"}
    if strength:
        if hardest <= ADD_LOAD_RPE:
            return {"action": "add load", "target": f"{_fmt(top_w + step)} × {reps}{u}", "why": why}
        return {"action": "add a rep", "target": f"{w} × {reps + 1}{u}", "why": why}
    floor, ceiling = high_rep_range if any(k in exercise.lower() for k in high_rep) else rep_range
    if reps > ceiling:
        return {"action": "add load", "target": f"{_fmt(top_w + step)} × {reps}{u}",
                "why": why + f": above the usual {floor}–{ceiling}, so the reps stay"}
    if reps == ceiling:
        return {"action": "add load", "target": f"{_fmt(top_w + step)} × {floor}{u}",
                "why": why + f": the top of the {floor}–{ceiling} range"}
    return {"action": "add a rep", "target": f"{w} × {reps + 1}{u}", "why": why}

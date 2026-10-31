"""Deterministic computations. No model in this file by design.

Every number the app shows or acts on is produced here, in plain arithmetic, so it can
be checked by hand. The LLM layer explains these outputs; it never produces them.
"""
import os, re, statistics as st, threading
from dataclasses import dataclass
from datetime import date, timedelta
from estebot import config, core
from estebot.web import db

# The trend math lives in core.py so the CLI and the app cannot disagree about it.
ols_slope_per_week = core.ols_slope_per_week
rolling = core.rolling

ISO = "%Y-%m-%d"
def d(s): return date.fromisoformat(s)
def s(x): return x.isoformat()
def daterange(a, b):
    cur = d(a) if isinstance(a, str) else a
    end = d(b) if isinstance(b, str) else b
    while cur <= end:
        yield cur; cur += timedelta(days=1)

# ---------------------------------------------------------------- coverage gates
def excused_days(con, start, end):
    """Days inside [start,end] covered by an annotation marked excused."""
    out = set()
    for r in con.execute("SELECT start_date, end_date FROM annotation WHERE excused=1"):
        a = d(r["start_date"]); b = d(r["end_date"]) if r["end_date"] else a
        for day in daterange(max(a, d(start)), min(b, d(end))):
            out.add(s(day))
    return out

def coverage(con, table, start, end, date_col="date", require_complete=False, column=None):
    """Returns (logged, required_denominator, excused, pct). Excused days leave the
    denominator - a family emergency is not a failure of discipline. With `column`, a day
    counts only if that column has a value (an HRV prediction counts nights with HRV, not
    rows in the daily table)."""
    q = f"SELECT {date_col} AS dt FROM {table} WHERE {date_col} BETWEEN ? AND ?"
    if require_complete: q += " AND complete=1"
    if column:
        if not column.isidentifier():
            raise ValueError(f"not a column name: {column!r}")
        q += f" AND {column} IS NOT NULL"
    logged = {r["dt"] for r in con.execute(q, (start, end))}
    all_days = {s(x) for x in daterange(start, end)}
    exc = excused_days(con, start, end) - logged
    denom = len(all_days - exc)
    return len(logged), denom, len(exc), (len(logged)/denom*100 if denom else 0.0)

def longest_gap(con, table, start, end, date_col="date"):
    logged = {r["dt"] for r in con.execute(
        f"SELECT {date_col} AS dt FROM {table} WHERE {date_col} BETWEEN ? AND ?", (start, end))}
    worst = run = 0
    for day in daterange(start, end):
        run = 0 if s(day) in logged else run + 1
        worst = max(worst, run)
    return worst

# ---------------------------------------------------------------- trend
def weight_series(con, start, end):
    return [(d(r["date"]), r["weight_lb"]) for r in con.execute(
        "SELECT date, weight_lb FROM weight WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end))]

def weight_status(con, day, goal_lb, max_loss_pct_per_week=None, window_days=28):
    """Where body weight is and where it is heading. Every figure from core functions.

    current: the 14-day trend on `day` (the line the chart draws), not one morning's reading.
    rate: OLS slope over the weigh-ins in the last `window_days` CALENDAR days.
    too_fast: losing faster than max_loss_pct_per_week of body weight, the line set in
    estebot.toml for keeping muscle on the cut."""
    start = s(d(day) - timedelta(days=window_days - 1))
    recent = weight_series(con, start, day)
    hist = weight_series(con, s(d(day) - timedelta(days=13)), day)
    current = core.trailing_mean(hist, d(day))
    rate = core.ols_slope_per_week(recent)
    out = {"current": current, "rate": rate, "n": len(recent), "goal": goal_lb,
           "weeks": None, "eta": None, "pct_per_week": None, "too_fast": False}
    if current is None:
        return out
    if rate is not None:
        out["pct_per_week"] = -rate / current * 100
        out["too_fast"] = (max_loss_pct_per_week is not None
                           and out["pct_per_week"] > max_loss_pct_per_week)
    wk = core.weeks_to_goal(current, rate, goal_lb)
    if wk is not None:
        out["weeks"] = wk
        out["eta"] = d(day) + timedelta(days=round(wk * 7))
    return out

def weight_changes(con, day, min_pct, max_pct, min_weighins=4):
    """Week over week and month over month, each judged against the planned loss range.

    wow: mean of the 7 days ending `day` vs the 7 before (needs min_weighins in each week;
         a single week swings 2-3 lb on water, so a thin week says nothing).
    mom: mean of the 28 days ending `day` vs the 28 before, as a weekly rate.
    Each: change_lb (latest window minus earlier), rate_per_week, status/pct via core.pace."""
    def window(end_offset, days):
        b = d(day) - timedelta(days=end_offset)
        pts = weight_series(con, s(b - timedelta(days=days - 1)), s(b))
        return [v for _, v in pts]
    out = {}
    for key, days, weeks, need in (("wow", 7, 1, min_weighins), ("mom", 28, 4, min_weighins * 2)):
        now, before = window(0, days), window(days, days)
        if len(now) < need or len(before) < need:
            out[key] = {"status": None, "n": (len(now), len(before))}; continue
        change = st.mean(now) - st.mean(before)
        rate = change / weeks
        status, pct = core.pace(rate, st.mean(now), min_pct, max_pct)
        out[key] = {"change_lb": change, "rate": rate, "status": status, "pct": pct,
                    "n": (len(now), len(before))}
    w, m = out["wow"], out["mom"]
    out["trend"] = None
    if w.get("pct") is not None and m.get("pct") is not None:
        # Called only when the week differs from the month by more than half the planned range.
        if w["pct"] < m["pct"] - (max_pct - min_pct) / 2:
            out["trend"] = "slowing"
        elif w["pct"] > m["pct"] + (max_pct - min_pct) / 2:
            out["trend"] = "speeding_up"
    return out

# ---------------------------------------------------------------- readiness
# Training load uses Apple's exercise_min: it is the only daily-complete load signal
# and it captures cardio and incidental activity, not just logged lifting.
def _load_series(con, end, days):
    start = s(d(end) - timedelta(days=days-1))
    got = {r["date"]: (r["exercise_min"] or 0.0) for r in con.execute(
        "SELECT date, exercise_min FROM daily WHERE date BETWEEN ? AND ?", (start, end))}
    return [got.get(s(x), 0.0) for x in daterange(start, end)]

def acwr(con, day):
    """Acute:chronic workload ratio - 7-day mean load over 28-day mean load.

    `day` must be the last COMPLETED day. Readiness is a morning decision, so today's
    training has not happened yet and must not be an input - see readiness().
    >1.5 is the injury-risk zone; <0.8 means detrained relative to recent norm.
    This is the metric that would have flagged a big session straight off a near-idle
    week, the setup for an injury the next day."""
    acute, chronic = _acwr_parts(con, day)
    return (acute / chronic) if chronic > 0 else None

def _acwr_parts(con, day):
    """The two means acwr() divides: exercise minutes a day over 7 days and over 28."""
    return st.mean(_load_series(con, day, 7)), st.mean(_load_series(con, day, 28))

def _metric_values(con, day, col, days, skip=0):
    """Non-null readings of `col` over `days` days ending `skip` days before `day`."""
    end = d(day) - timedelta(days=skip)
    return [r[col] for r in con.execute(
        f"SELECT {col} FROM daily WHERE date BETWEEN ? AND ? AND {col} IS NOT NULL",
        (s(end - timedelta(days=days-1)), s(end)))]

LIFT_STREAK_MIN_SETS = 5   # fewer than 5 sets is not a lifting session
NOT_TRAINING = ("Walking",)  # a logged walk alone does not make a training day

def is_light_movement(activity):
    """A walk: exercise, but light. It shows on the calendar and counts in Apple's exercise
    minutes (so in training load), but never makes a training day. The incline treadmill
    walks are cardio, not rest, and the calendar used to file a walk-only day as plain rest."""
    return activity in NOT_TRAINING

def is_training_day(con, day, min_sets=LIFT_STREAK_MIN_SETS):
    """A lifting session of >= min_sets, or any Apple workout other than a walk.

    A rest day is one with neither. Changed from lifting-only after a week that went rest,
    lifting, a non-lifting workout plus walks, lifting, lifting. Lifting-only called that a
    1-day streak because the non-lifting day broke it; the person counted 4, and it is their
    rest day that the >= 6-day rule protects. This counts more days as training, so the
    rest rule fires sooner, never later."""
    r = con.execute("SELECT n_sets FROM lift_session WHERE date=?", (day,)).fetchone()
    if r and (r["n_sets"] or 0) >= min_sets:
        return True
    q = f"SELECT 1 FROM workout WHERE date=? AND activity NOT IN ({','.join('?' * len(NOT_TRAINING))})"
    return con.execute(q, (day, *NOT_TRAINING)).fetchone() is not None

def days_since_rest(con, day, min_sets=LIFT_STREAK_MIN_SETS, horizon=14):
    """Consecutive training days (is_training_day) up to and including `day`."""
    n, cur = 0, d(day)
    for _ in range(horizon):
        if not is_training_day(con, s(cur), min_sets):
            break
        n += 1; cur -= timedelta(days=1)
    return n

def last_rest_day(con, day, horizon=30):
    """The most recent day on or before `day` that was not a training day, or None."""
    cur = d(day)
    for _ in range(horizon):
        if not is_training_day(con, s(cur)):
            return s(cur)
        cur -= timedelta(days=1)
    return None

def is_open(con, day, today=None):
    """True while `day` hasn't happened yet or is still going and has no training logged:
    an unfinished day isn't a rest day. Readiness for today said "go hard" while the
    calendar filed today as rest and 'days since rest' reset to 0."""
    return day >= (today or date.today().isoformat()) and not is_training_day(con, day)

def training_days(con, start, end, today=None):
    """Per day over [start, end]: training or rest (is_training_day, the same rule as
    'days since rest') and what was done, for the calendar. Days after `end` are not here.

    kind splits rest in two: 'movement' when the only exercise was light (a walk), else
    'rest'. It never changes `training`, so the calendar and 'days since rest' still agree.
    Today with nothing logged yet is 'open', not rest (is_open)."""
    lift = {r["date"]: dict(r) for r in con.execute(
        """SELECT date, n_exercises, n_sets, hard_sets, total_volume_lb, muscle_groups, session_min
           FROM lift_session WHERE date BETWEEN ? AND ?""", (start, end))}
    work = {}
    for r in con.execute("SELECT date, activity, duration_min FROM workout "
                         "WHERE date BETWEEN ? AND ? ORDER BY date, start", (start, end)):
        work.setdefault(r["date"], []).append((r["activity"], r["duration_min"]))
    mins = {r["date"]: r["exercise_min"] for r in con.execute(
        "SELECT date, exercise_min FROM daily WHERE date BETWEEN ? AND ?", (start, end))}
    out = {}
    for x in daterange(start, end):
        k = s(x)
        training, workouts = is_training_day(con, k), work.get(k, [])
        kind = ("training" if training else
                "movement" if any(is_light_movement(a) for a, _ in workouts) else
                "open" if is_open(con, k, today) else "rest")
        out[k] = {"training": training, "kind": kind, "lift": lift.get(k),
                  "workouts": workouts, "exercise_min": mins.get(k)}
    return out

# Sick or injured, from the notes or the check-in toggles. The sick words are core.SICK. A sore
# back is not on the injured list: it is a check-in level, and a week of mild soreness would
# ring every day.
INJURED = re.compile(r"injur|spasm|tweak", re.I)

def unwell_days(con, start, end):
    """{date: {"sick": bool, "injured": bool, "notes": [...]}} over [start, end], for days a note
    says sick or injured or the check-in has either toggle on. A ranged note covers every day
    in its range; annotations typed 'injury' count without the keyword. Either source is
    enough: turning a toggle off does not cancel a note that says sick."""
    out = {}
    def mark(day, sick, injured, text):
        e = out.setdefault(day, {"sick": False, "injured": False, "notes": []})
        e["sick"] |= sick
        e["injured"] |= injured
        if text and text not in e["notes"]:     # a saved check-in can carry the note's text
            e["notes"].append(text)
    for r in con.execute(
            """SELECT date, sick, injured, note FROM checkin
               WHERE date BETWEEN ? AND ? AND (sick = 1 OR injured = 1)""", (start, end)):
        mark(r["date"], bool(r["sick"]), bool(r["injured"]), r["note"])
    for r in con.execute(
            """SELECT start_date, COALESCE(end_date, start_date) AS end_date, type, note
               FROM annotation WHERE start_date <= ? AND COALESCE(end_date, start_date) >= ?
               ORDER BY start_date""", (end, start)):
        text = r["note"] or ""
        sick, injured = bool(core.SICK.search(text)), r["type"] == "injury" or bool(INJURED.search(text))
        if not (sick or injured):
            continue
        for x in daterange(max(r["start_date"], start), min(r["end_date"], end)):
            mark(s(x), sick, injured, text)
    return out

# What the verdict reads, as (table, date column). A day "has data" if any of them has a row.
VERDICT_INPUTS = (("weight", "date"), ("daily", "date"), ("sleep", "night"),
                  ("lift_session", "date"), ("nutrition", "date"), ("checkin", "date"))

def default_day(con, today):
    """The day the page opens on. Today while the data is current, meaning something the
    verdict reads is dated today or yesterday (load and the check-in are read through
    yesterday, so a morning before the day's sync still counts). Otherwise the last day
    with data, so a data set that ends before today (a demo, or a sync that stopped)
    does not open on an empty day."""
    latest = max((r[0] for table, col in VERDICT_INPUTS
                  for r in con.execute(f"SELECT MAX({col}) FROM {table} WHERE {col} <= ?", (today,))
                  if r[0]), default=None)
    if latest is None or latest >= s(d(today) - timedelta(days=1)):
        return today
    return latest

def _signal(area, level, reasons):
    """reasons: plain strings, or (level, text) for a part with its own level; those are
    put worst first, so the part that set the signal's level is the one read first.
    Reasons say what was measured against what, in words; thresholds stay in the code."""
    rank = lambda r: -core.LEVELS.index(r[0]) if isinstance(r, tuple) and r[0] else 0
    rs = sorted((r for r in reasons if r and (not isinstance(r, tuple) or r[1])), key=rank)
    return {"area": area, "level": level, "reasons": [r[1] if isinstance(r, tuple) else r for r in rs]}

def _load_signal(con, prior):
    r = acwr(con, prior)
    mono = core.monotony(_load_series(con, prior, 7))
    # Low is amber too: a spike straight off a layoff week is the setup that once preceded
    # an injury the next day.
    lv_r = None if r is None else ("red" if r > 1.5 else "amber" if (r > 1.3 or r < 0.8) else "green")
    lv_m = None if mono is None else ("red" if mono > 2.5 else "amber" if mono > 2.0 else "green")
    acute, chronic = _acwr_parts(con, prior)
    meaning = ("" if r is None else ": a big jump, keep today moderate" if r > 1.3
               else ": a light week, build back up rather than going all out" if r < 0.8
               else ": within the normal range")
    ratio = r is not None and (f"last 7 days averaged {acute:.0f} exercise min a day vs {chronic:.0f} "
                               f"over the last 4 weeks ({r:.2f}×){meaning}")
    alike = (None if mono is None else "no easy day in the last 7, the same exercise minutes every day"
             if mono == float("inf")
             else f"no easy day in the last 7, every day about as long as the next (monotony {mono:.1f})"
             if lv_m != "green"
             else f"a mix of harder and easier days in the last 7 (monotony {mono:.1f})")
    return _signal("load", core.worst(lv_r, lv_m), [(lv_r, ratio), (lv_m, alike)]), r

def _effort_signal(con, prior):
    """Session RPE lately vs the month before: the same work feeling harder is fatigue."""
    rpe = lambda a, b: [x["mean_rpe"] for x in con.execute(
        "SELECT mean_rpe FROM lift_session WHERE date BETWEEN ? AND ? AND mean_rpe IS NOT NULL",
        (a, b))]
    recent = rpe(s(d(prior) - timedelta(days=6)), prior)
    base = rpe(s(d(prior) - timedelta(days=34)), s(d(prior) - timedelta(days=7)))
    if len(recent) < 2 or len(base) < 4:
        return _signal("effort", None, ["not enough sessions with RPE yet"])
    m, bm = st.mean(recent), st.mean(base)
    lv = "red" if m - bm >= 1.5 else "amber" if (m - bm >= 0.75 or m >= 9.0) else "green"
    return _signal("effort", lv, [f"sessions felt RPE {m:.1f} over the last 7 days vs {bm:.1f} in the 4 weeks before"])

def _sleep_signal(con, day):
    nights = lambda a, b: [x["asleep_hr"] for x in con.execute(
        "SELECT asleep_hr FROM sleep WHERE night BETWEEN ? AND ? AND asleep_hr IS NOT NULL",
        (a, b))]
    prior = s(d(day) - timedelta(days=1))
    last = con.execute("SELECT asleep_hr FROM sleep WHERE night=?", (prior,)).fetchone()
    last = last["asleep_hr"] if last else None
    three = nights(s(d(day) - timedelta(days=3)), prior)
    base = nights(s(d(day) - timedelta(days=33)), s(d(day) - timedelta(days=4)))
    lv_l = None if last is None else ("red" if last < 5 else "amber" if last < 6 else "green")
    lv_3 = None
    if len(three) >= 2 and len(base) >= 10:
        gap = st.mean(base) - st.mean(three)
        lv_3 = "red" if gap > 1.5 else "amber" if gap > 0.75 else "green"
    return _signal("sleep", core.worst(lv_l, lv_3), [
        (lv_l, last is not None and f"{last:.1f} h last night"),
        (lv_3, lv_3 and f"{st.mean(three):.1f} h a night over the last 3 nights vs {st.mean(base):.1f} h over the 30 before")]), last

def _recovery_signal(con, day):
    # This week against the 30 days before it, never including it: a baseline that holds
    # the week being judged drags toward it and hides the change.
    week, base = _metric_values(con, day, "hrv_sdnn", 7), _metric_values(con, day, "hrv_sdnn", 30, 7)
    rweek, rbase = _metric_values(con, day, "resting_hr", 7), _metric_values(con, day, "resting_hr", 30, 7)
    hrv7 = st.mean(week) if week else None
    mu = st.mean(base) if len(base) >= 5 else None
    rhr7 = st.mean(rweek) if rweek else None
    rmu = st.mean(rbase) if len(rbase) >= 5 else None
    z = core.weekly_shift_z(week, base)
    lv_h = None if z is None else ("red" if z <= -1.5 else "amber" if z <= -1.0 else "green")
    lv_r = None
    if rhr7 and rmu:
        lv_r = "red" if rhr7 > rmu + 5 else "amber" if rhr7 > rmu + 3 else "green"
    sig = _signal("recovery", core.worst(lv_h, lv_r), [
        (lv_h, z is not None and f"HRV {hrv7:.0f} over the last 7 days vs {mu:.0f} over the 30 before"),
        (lv_r, lv_r and f"resting HR {rhr7:.0f} vs {rmu:.0f}, same windows")])
    return sig, dict(hrv7=hrv7, hrv_baseline=mu, hrv_z=z, rhr7=rhr7, rhr_baseline=rmu)

def _feel_signal(con, day):
    """The latest check-in from the last two days (notes become check-ins at each sync)."""
    ci = con.execute("SELECT * FROM checkin WHERE date BETWEEN ? AND ? ORDER BY date DESC LIMIT 1",
                     (s(d(day) - timedelta(days=1)), day)).fetchone()
    poor = [r["date"] for r in con.execute(
        """SELECT date FROM checkin WHERE date <= ? AND date >= ?
             AND inter_set_recovery IS NOT NULL AND inter_set_recovery <= 1 ORDER BY date""",
        (day, s(d(day) - timedelta(days=13))))]
    levels, reasons = [], []
    if ci:
        when = "today" if ci["date"] == day else "yesterday"
        if ci["energy"] is not None:
            lv = "red" if ci["energy"] <= 0 else "amber" if ci["energy"] == 1 else "green"
            levels.append(lv); reasons.append(f"energy {ci['energy']}/3 {when}")
        if ci["back_reported"]:
            back = ci["back"] or "ok"
            lv = {"spasm": "red", "sore": "amber"}.get(back, "green")
            levels.append(lv); reasons.append(f"back {back} {when}")
        if ci["inter_set_recovery"] is not None and ci["inter_set_recovery"] <= 1:
            levels.append("amber"); reasons.append(f"recovery between sets poor {when}")
    if len(poor) >= 3:
        levels.append("amber"); reasons.append(f"recovery between sets poor on {len(poor)} of 14 days")
    if not levels:
        reasons.append("nothing in your notes for today or yesterday")
    return _signal("feel", core.worst(*levels), reasons), ci, poor

def _nutrition_signal(con, day, goals, body_lb, rate, days=4):
    """The last few complete days against maintenance as the scale implies it, and protein
    against the configured target - all through nutrition.py, so the page and this signal
    cannot compute them differently."""
    from estebot.analysis import nutrition
    prior = s(d(day) - timedelta(days=1))
    kc, n = nutrition.intake(con, prior, days)
    pr = nutrition.protein(con, prior, body_lb, days)
    if n < 2:
        return _signal("nutrition", None, [f"under 2 complete days logged in the last {days}"])
    maint = nutrition.maintenance(con, prior, rate)
    target = nutrition.protein_target(goals, body_lb)
    levels, reasons = [], []
    over = f"the last {days} days" if n == days else f"{n} complete days of the last {days}"
    if maint:
        gap = maint - kc
        levels.append("red" if gap > 1500 else "amber" if gap > 1000 else "green")
        reasons.append((levels[-1], f"eating {kc:,.0f} kcal a day over {over} vs ~{maint:,.0f} to maintain (4-week trend)"))
    if target and pr["mean"] is not None:
        levels.append("red" if pr["mean"] < 0.6 * target else "amber" if pr["mean"] < 0.8 * target else "green")
        reasons.append((levels[-1], f"protein {pr['mean']:.0f} g a day over the same days vs {target:.0f} g target"))
    return _signal("nutrition", core.worst(*levels), reasons)

def _pace_signal(con, day, goals):
    lo, hi = (goals or {}).get("min_loss_pct_per_week", 0.5), (goals or {}).get("max_loss_pct_per_week", 1.0)
    m = weight_changes(con, day, lo, hi)["mom"]
    if m.get("pct") is None:
        return _signal("pace", None, ["not enough weigh-ins for a 4-week comparison"])
    lv = "red" if m["pct"] > 1.5 * hi else "amber" if m["pct"] > hi else "green"
    return _signal("pace", lv, [f"losing {m['pct']:.2f}% of body weight a week (last 4 weeks vs the 4 before)"
                                + (", fast enough to cost recovery" if lv != "green" else "")])

POSITIVE_LAST_SESSION = 0.8   # share of targets met that makes a strong last session

def _last_session(con, day, min_sets=LIFT_STREAK_MIN_SETS):
    r = con.execute("SELECT MAX(date) d FROM lift_session WHERE date < ? AND n_sets >= ?",
                    (day, min_sets)).fetchone()
    return session_outcome(con, r["d"]) if r and r["d"] else None

def readiness(con, day, goals, voting="auto", graded=None):
    """Seven areas, each green/amber/red/no-data with its reasons; the call from those that
    have earned a vote (core.evidence_call). `voting` is "auto" (each area's record over the
    sessions before `day`, voting_areas) or the areas to count. `graded` passes through to
    earned() for "auto".

    Load is read through YESTERDAY (a morning decision; today's session hasn't happened).
    Recovery and sleep are read through TODAY (measured overnight, there when you wake).
    Days since rest is reported, not used: monotony carries what a run of identical days
    costs, without a fixed day count."""
    prior = s(d(day) - timedelta(days=1))
    ws = weight_status(con, day, goals.get("target_weight_lb", 0) or 0)
    load, r_acwr = _load_signal(con, prior)
    sleep, last_sleep = _sleep_signal(con, day)
    recovery, rec = _recovery_signal(con, day)
    feel, ci, poor = _feel_signal(con, day)
    signals = [load, _effort_signal(con, prior), sleep, recovery, feel,
               _nutrition_signal(con, day, goals, ws["current"], ws["rate"]),
               _pace_signal(con, day, goals)]
    spasm = bool(ci and ci["back_reported"] and ci["back"] == "spasm")
    if spasm:
        feel["reasons"].append("a back spasm means rest, whatever else says (your rule)")
    levels = {x["area"]: x["level"] for x in signals}

    ramp = None
    recent5 = st.mean(_load_series(con, prior, 5))
    chronic = st.mean(_load_series(con, prior, 28))
    if chronic > 0 and recent5 < 0.25 * chronic:
        cap = round(chronic * 1.3)
        ramp = (f"Coming off a low-load stretch ({recent5:.0f} vs {chronic:.0f} min/day norm). "
                f"Cap today near {cap} min rather than making up the deficit at once.")
    # Safety rules hold the day whatever the evidence says: they are about not getting hurt,
    # and a few sick days a year are too few to ever earn a vote.
    holds = []
    unwell = unwell_days(con, prior, day)
    if any(e["sick"] for e in unwell.values()):
        holds.append("sick today or yesterday")
    if any(e["injured"] for e in unwell.values()):
        holds.append("injured today or yesterday")
    if ramp:
        holds.append("back from a near-idle stretch")
    # Reasons to go hard, each (present, words); only those whose record earned it count. Only
    # this morning's check-in: yesterday's says how yesterday went, not today.
    reasons = {}
    if ci and ci["date"] == day and ci["energy"] is not None:
        reasons["check-in"] = (ci["energy"] >= 2 and feel["level"] == "green",
                               f"you checked in this morning with energy {ci['energy']}/3")
    last = _last_session(con, day)
    if last:
        met = sum(x["met"] for x in last["exercises"])
        reasons["last session"] = (last["hit"] >= POSITIVE_LAST_SESSION,
                                   f"your last session met {met} of {len(last['exercises'])} targets")
    if voting == "auto":
        got = earned(con, day, goals, graded)
        voting, hard_ok = got["voting"], got["hard"]
    else:
        voting, hard_ok = list(voting), []
    positive = next((words for k, (on, words) in reasons.items() if on and k in hard_ok), None)
    verdict = core.evidence_call(levels, voting, spasm, holds, positive)
    return {
        "date": day, "verdict": verdict, "score": core.readiness_score(levels, verdict),
        "signals": signals, "ramp": ramp, "checkin": ci, "voting": voting, "holds": holds,
        "positive": positive, "hard_reasons": reasons, "forced_rest": spasm,
        "subjective_review": bool(ci["needs_review"]) if ci and ci["date"] == day else False,
        "poor_recovery_days": poor, "acwr": r_acwr, **rec,
        "streak": days_since_rest(con, prior), "last_sleep": last_sleep, "today_open": is_open(con, day),
        # an unfinished day counts toward neither: today's training isn't in yet
        "streak_now": days_since_rest(con, prior if is_open(con, day) else day),
        "last_rest": last_rest_day(con, prior if is_open(con, day) else day),
    }

_earned_cache = {}
_graded_cache = {}    # database file -> (its mtime when graded, graded_sessions rows, goals)
_graded_lock = threading.Lock()

def graded_for(con, goals):
    """Every session in the database graded (graded_sessions), cached per database file until
    it changes. A row doesn't depend on the day being viewed, so changing the day costs only
    the tally; regrading 1,100 sessions took ~10 s on the server, on every day not yet
    viewed. One at a time, so the warmer (web/main.py) and a request don't both grade."""
    path = con.execute("PRAGMA database_list").fetchone()[2]
    if not path:
        return graded_sessions(con, "1900-01-01", "9999-12-31", goals)
    with _graded_lock:
        mtime = os.path.getmtime(path)
        hit = _graded_cache.get(path)
        if not (hit and hit[0] == mtime):
            hit = _graded_cache[path] = (mtime, graded_sessions(con, "1900-01-01", "9999-12-31", goals), goals)
        return hit[1]

def stale_graded():
    """[(database file, goals)] graded before and changed since: what the warmer regrades."""
    return [(path, goals) for path, (mtime, _, goals) in list(_graded_cache.items())
            if os.path.exists(path) and os.path.getmtime(path) != mtime]

def earned(con, day, goals, graded=None):
    """{"voting": areas whose flagged days went worse, "hard": reasons to go hard whose days
    went better, "evidence": the table they came from}, over every session before `day`. Cached per database
    file, day and the file's last change; the sessions are graded once per change (graded_for)
    and only tallied per day. `graded` passes rows already made, for a caller asking about
    many days."""
    path = con.execute("PRAGMA database_list").fetchone()[2]
    key = (path, day, os.path.getmtime(path)) if path else None
    if key not in _earned_cache:
        ev = evidence(con, "1900-01-01", s(d(day) - timedelta(days=1)), goals,
                      graded=graded if graded is not None else graded_for(con, goals))
        got = {"voting": ev["voting"], "hard": ev["hard"], "evidence": ev}
        if key is None:
            return got
        for k in [k for k in _earned_cache if k[0] == path and k[2] != key[2]]:
            del _earned_cache[k]                      # a changed file's old tallies
        _earned_cache[key] = got
    return _earned_cache[key]

def recovery_series(con, start, end):
    """The readiness inputs as daily series over [start, end], for the page's charts.

    Same columns and the same acwr() the verdict uses, so a chart can never show a number
    the verdict did not see. Sleep is keyed by night (the evening it began)."""
    q = lambda col: [(d(r["date"]), r[col]) for r in con.execute(
        f"SELECT date, {col} FROM daily WHERE date BETWEEN ? AND ? AND {col} IS NOT NULL "
        "ORDER BY date", (start, end))]
    sleep = [(d(r["night"]), r["asleep_hr"]) for r in con.execute(
        "SELECT night, asleep_hr FROM sleep WHERE night BETWEEN ? AND ? AND asleep_hr IS NOT NULL "
        "ORDER BY night", (start, end))]
    ratio = [(x, v) for x in daterange(start, end) if (v := acwr(con, s(x))) is not None]
    return {"hrv": q("hrv_sdnn"), "rhr": q("resting_hr"), "sleep": sleep, "acwr": ratio}

def training_load_series(con, start, end):
    """Daily load over [start, end], every day present (0 when nothing was done), so the
    three charts share one x-axis: exercise minutes (Apple, all activity), hard sets and
    tonnage (lifting only; core.is_hard_set). Tonnage counts logged weight, so bodyweight
    work like pull-ups adds sets but no pounds."""
    mins = {r["date"]: r["exercise_min"] or 0.0 for r in con.execute(
        "SELECT date, exercise_min FROM daily WHERE date BETWEEN ? AND ?", (start, end))}
    lift = {r["date"]: r for r in con.execute(
        "SELECT date, hard_sets, total_volume_lb FROM lift_session WHERE date BETWEEN ? AND ?",
        (start, end))}
    days = list(daterange(start, end))
    get = lambda k, col: (lift[k][col] or 0) if k in lift else 0
    return {"minutes": [(x, mins.get(s(x), 0.0)) for x in days],
            "hard_sets": [(x, get(s(x), "hard_sets")) for x in days],
            "tonnage_klb": [(x, get(s(x), "total_volume_lb") / 1000) for x in days]}

# ---------------------------------------------------------------- strength
MAIN_LIFTS = ["Barbell Squat", "Barbell Deadlift", "Barbell Bench Press",
              "Barbell Bent-Over Row", "Barbell Military Press"]

def e1rm_window_best(con, start, end, lifts=None):
    lifts = lifts or MAIN_LIFTS
    out = {}
    for ex in lifts:
        r = con.execute(
            """SELECT MAX(best_e1rm_lb) m FROM e1rm
               WHERE exercise=? AND date BETWEEN ? AND ? AND best_e1rm_lb > 0""",
            (ex, start, end)).fetchone()
        if r and r["m"]: out[ex] = r["m"]
    return out

def e1rm_median_change(con, w1, w2, lifts=None):
    a = e1rm_window_best(con, *w1, lifts); b = e1rm_window_best(con, *w2, lifts)
    common = [k for k in a if k in b]
    if not common: return None, {}
    pct = {k: (b[k]-a[k])/a[k]*100 for k in common}
    return st.median(pct.values()), pct

if __name__ == "__main__":
    from estebot import config
    con = db.connect()
    goals = config.load().goals
    import sys
    # Spot-check: python -m estebot.analysis.engine YYYY-MM-DD [...] (default: today).
    for day in sys.argv[1:] or [date.today().isoformat()]:
        r = readiness(con, day, goals)
        print(f"\n{day}: {r['verdict'].upper()} {r['score']}"
              + (f"   ACWR {r['acwr']:.2f}" if r["acwr"] else ""))
        for x in r["signals"]: print(f"   [{x['level'] or '-'}] {x['area']}: {'; '.join(x['reasons'])}")
        if r["ramp"]: print(f"   RAMP: {r['ramp']}")

# ---------------------------------------------------------------- movement pattern
# Axial load = how much the movement compresses/shears the lumbar spine. Scored 0-3 by
# exercise name. A push/pull/legs rotation separates cleanly - Push is low-axial, Pull and
# Legs are not. When the lower back is the limiter, choosing the DAY is a bigger lever than
# choosing the intensity.
import re as _re
AXIAL = [
    (3, r"deadlift|barbell row|bent.?over row|clean|snatch|jerk|good morning|"
        r"farmer|carry|yoke|suitcase|back squat|^barbell squat|front squat|rack pull|shrug"),
    (2, r"squat|lunge|split squat|step.?up|hip thrust|romanian|stiff.?leg|"
        r"military press|overhead press|standing.*press|pull.?up|chin.?up|upright row"),
    (1, r"pulldown|seated row|cable row|incline|dip|bench press|arnold|"
        r"face pull|leg press|hack"),
]
def axial_score(exercise):
    for score, pat in AXIAL:
        if _re.search(pat, exercise, _re.I): return score
    return 0            # machines, isolation, seated/lying work

def session_axial(con, day):
    rows = con.execute("SELECT exercise FROM e1rm WHERE date=?", (day,)).fetchall()
    if not rows: return None
    return st.mean([axial_score(r["exercise"]) for r in rows])

DAY_TYPES = {
    "push": ("chest", "shoulders", "triceps"),
    "pull": ("back", "biceps", "carry", "olympic"),
    "legs": ("quads", "hamstrings", "calves"),
}
def day_type(groups, training=None):
    if not groups: return None
    g = set(groups.split("|"))
    best, score = None, 0
    for name, members in (training or TRAINING).day_types.items():
        hit = len(g & set(members))
        if hit > score: best, score = name, hit
    return best

def axial_by_day_type(con, day, days=150, training=None):
    """Mean axial load per day type, from the sessions logged in the `days` up to `day`.
    About five months: several of each day type, and recent enough to reflect the
    exercises in use now. Trailing rather than a fixed start date, so any history that
    has recent sessions gets a ranking."""
    acc = {}
    for r in con.execute(
        "SELECT date, muscle_groups FROM lift_session WHERE date BETWEEN ? AND ?",
        (s(d(day) - timedelta(days=days - 1)), day)):
        t = day_type(r["muscle_groups"], training)
        a = session_axial(con, r["date"])
        if t and a is not None: acc.setdefault(t, []).append(a)
    return {k: st.mean(v) for k, v in sorted(acc.items(), key=lambda kv: st.mean(kv[1]))}

def latest_checkin(con, day):
    return con.execute(
        "SELECT * FROM checkin WHERE date <= ? ORDER BY date DESC LIMIT 1", (day,)).fetchone()

def recommend_pattern(con, day, checkin, training=None):
    """Given back status, which day type is safe today. Returns (pattern, why) or None.
    `checkin` is the row readiness() used, so the suggestion and the verdict cite the same
    note."""
    c = checkin
    back = (c["back"] if c and c["back_reported"] else None) or "ok"
    if back == "ok": return None
    if back == "spasm":       # needs no ranking of day types, so it comes first
        return ("rest", "Back is spasming. No session — this is the rule you already use.")
    ranked = axial_by_day_type(con, day, training=training)
    if not ranked: return None
    safest = next(iter(ranked))
    if back == "sore":
        return (safest, f"Back is sore. {safest.title()} is your lowest spinal-load day "
                        f"(mean axial {ranked[safest]:.1f} of 3). Avoid rows, carries, cleans, "
                        f"squats and deadlift variants entirely.")
    return (safest, f"Back is tired. Favour {safest} today (mean axial "
                    f"{ranked[safest]:.1f} of 3) over the higher-load rotations "
                    + ", ".join(f"{k} {v:.1f}" for k, v in list(ranked.items())[1:]) + ".")


# Strength standards: which logged exercise stands for each standard lift.
STANDARD_LIFTS = (("squat", "Squat", "Barbell Squat"),
                  ("bench", "Bench press", "Barbell Bench Press"),
                  ("deadlift", "Deadlift", "Barbell Deadlift"),
                  ("press", "Overhead press", "Barbell Military Press"))

def strength_standards(con, day, body_lb, goal_lb=None, weeks=8):
    """Each main lift's best e1RM over the last `weeks` against bodyweight-scaled standards,
    now and at the goal weight with the same lifts. body_lb is the 14-day trend, the same
    number the weight card leads with. All math in core."""
    start = s(d(day) - timedelta(weeks=weeks) + timedelta(days=1))
    best = e1rm_window_best(con, start, day, [ex for _, _, ex in STANDARD_LIFTS])
    rows = []
    for key, label, ex in STANDARD_LIFTS:
        rm = best.get(ex)
        row = {"lift": key, "label": label, "e1rm": rm}
        if rm and body_lb:
            row["ratio"] = rm / body_lb
            row["level"], row["next_at"] = core.strength_level(key, rm, body_lb)
            if goal_lb:
                row["goal_level"], _ = core.strength_level(key, rm, goal_lb)
        rows.append(row)
    sbd = [best.get(ex) for k, _, ex in STANDARD_LIFTS if k != "press"]
    total = sum(sbd) if all(sbd) else None
    return {"rows": rows, "weeks": weeks, "body_lb": body_lb, "goal_lb": goal_lb, "total": total,
            "dots": core.dots(total, body_lb) if total and body_lb else None,
            "goal_dots": core.dots(total, goal_lb) if total and goal_lb else None}


# ---------------------------------------------------------------- training plan
# Defaults for the config's [training] section: push, pull, legs, each in an A and a B
# version told apart by the main lift. A deadlift day stays pull B in either stance.
ROTATION = ("push", "pull", "legs")
VARIANTS = {
    "push": (("A", ("Barbell Bench Press",)), ("B", ("Barbell Military Press",))),
    "pull": (("A", ("Pendlay Row", "Barbell Bent-Over Row")),
             ("B", ("Barbell Deadlift", "Sumo Deadlift"))),
    "legs": (("A", ("Barbell Squat",)), ("B", ("Barbell Front Squat",))),
}

@dataclass(frozen=True)
class Training:
    """One person's split and rep ranges. A key the config leaves out keeps its default; a
    table it gives (variants, day_types) replaces the default whole, so an upper/lower split
    does not inherit push, pull and legs."""
    rotation: tuple
    variants: dict
    day_types: dict
    high_rep: tuple
    rep_range: tuple
    high_rep_range: tuple

    @classmethod
    def from_config(cls, raw):
        t = cls(rotation=tuple(raw.get("rotation", ROTATION)),
                variants={k: tuple((n, tuple(m)) for n, m in v.items())
                          for k, v in raw["variants"].items()} if "variants" in raw else VARIANTS,
                day_types={k: tuple(v) for k, v in raw["day_types"].items()}
                          if "day_types" in raw else DAY_TYPES,
                high_rep=tuple(raw.get("high_rep", core.HIGH_REP)),
                rep_range=tuple(raw.get("rep_range", core.REP_RANGE)),
                high_rep_range=tuple(raw.get("high_rep_range", core.HIGH_REP_RANGE)))
        missing = [k for k in t.rotation if not t.variants.get(k) or k not in t.day_types]
        if missing:
            raise config.ConfigError(f"[training] rotation names {', '.join(missing)} "
                                     "without matching variants and day_types entries")
        return t

TRAINING = Training.from_config(config.load().training)

def _session_exercises(con, day):
    return [r["exercise"] for r in con.execute(
        "SELECT exercise FROM lift_set WHERE date=? GROUP BY exercise ORDER BY MIN(rowid)", (day,))]

def _variant(kind, exercises, training):
    for name, mains in training.variants.get(kind, ()):
        hit = [e for e in exercises if e in mains]
        if hit:
            return name, hit[0]
    return None, None

def _last_sets(con, exercise, before):
    r = con.execute("SELECT MAX(date) d FROM lift_set WHERE exercise=? AND date < ?",
                    (exercise, before)).fetchone()
    if not r or not r["d"]:
        return None, []
    rows = con.execute("""SELECT weight_lb, reps, rpe FROM lift_set
                          WHERE exercise=? AND date=? AND COALESCE(set_type,'') != 'warmup'
                          ORDER BY set_idx""", (exercise, r["d"])).fetchall()
    return r["d"], [(x["weight_lb"], x["reps"], x["rpe"]) for x in rows]

def session_outcome(con, day, training=None):
    """How a day's lifting went against its plan: for each exercise done, the goal
    core.progression set from the last time it was done, whether the day's working sets
    met it, and the RPE of its first top set. Rebuilt from lift_set, so any past session can
    be graded, not just those since calls were recorded. None on a day with nothing to grade."""
    training = training or TRAINING
    done = {}
    for r in con.execute("""SELECT exercise, weight_lb, reps, rpe FROM lift_set
                            WHERE date=? AND COALESCE(set_type,'') != 'warmup'
                            ORDER BY rowid""", (day,)):
        done.setdefault(r["exercise"], []).append((r["weight_lb"], r["reps"], r["rpe"]))
    out = []
    for ex, sets in done.items():
        _, before = _last_sets(con, ex, day)
        p = core.progression(ex, before, training.high_rep, training.rep_range, training.high_rep_range)
        if p is None:
            continue
        top_w = max((w or 0.0) for w, _, _ in sets)
        out.append({"exercise": ex, "action": p["action"], "target": p["target"],
                    "met": core.goal_met(p["goal"], sets),
                    "top_rpe": next((rpe for w, _, rpe in sets if (w or 0.0) == top_w), None)})
    if not out:
        return None
    return {"date": day, "exercises": out, "hit": sum(x["met"] for x in out) / len(out)}

PHASE_DAYS = 45   # a session is compared with the sessions within this many days either side

def graded_sessions(con, start, end, goals, training=None, min_sets=LIFT_STREAK_MIN_SETS):
    """Every session in [start, end] graded (session_outcome) and paired with the morning
    call readiness() makes for its day under today's rules, before any area votes. The slow
    part of evidence(); a session's row doesn't depend on the window around it, so rows made
    once can be tallied over many windows."""
    rows = []
    for r in con.execute("SELECT date FROM lift_session WHERE date BETWEEN ? AND ? AND n_sets >= ? "
                         "ORDER BY date", (start, end, min_sets)):
        o = session_outcome(con, r["date"], training)
        if o is None:
            continue
        call = readiness(con, r["date"], goals, voting=())
        rows.append({"date": r["date"], "hit": o["hit"], "call": call,
                     "levels": {x["area"]: x["level"] for x in call["signals"]}})
    return rows

def evidence(con, start, end, goals, training=None, min_sets=LIFT_STREAK_MIN_SETS, graded=None):
    """Each readiness area's track record: did sessions go worse on days it was flagged?

    Every session in [start, end] is graded and paired with its morning call
    (graded_sessions; pass `graded` to tally rows already made). A session's score is its share
    of targets met less the mean of the sessions within PHASE_DAYS either side, so years
    that went well or badly as a whole don't read as an input's effect. Per area, flagged
    (amber or red) against clear (green) goes through core.flag_effect; days with no data
    for an area count on neither side. Also: how sessions went after each verdict, and
    candidates: inputs outside the call, found by searching the history and tracked until
    new sessions confirm them."""
    if graded is None:
        graded = graded_sessions(con, start, end, goals, training, min_sets)
    rows = [dict(x) for x in graded if start <= x["date"] <= end]   # copies: the tally adds fields
    n = [d(x["date"]).toordinal() for x in rows]
    for i, x in enumerate(rows):
        near = [y["hit"] for j, y in enumerate(rows) if j != i and abs(n[j] - n[i]) <= PHASE_DAYS]
        x["vs_phase"] = x["hit"] - st.mean(near) if near else None
    rows = [x for x in rows if x["vs_phase"] is not None]
    areas = list(rows[0]["levels"]) if rows else []
    by_area = {a: core.flag_effect([x["vs_phase"] for x in rows if x["levels"][a] in ("amber", "red")],
                                   [x["vs_phase"] for x in rows if x["levels"][a] == "green"])
               for a in areas}
    # Found in Oct 2026 among 8 candidates searched, so it is watched, not trusted: a search
    # that tries enough inputs finds one that looks real by chance.
    after_miss = [(prev["hit"] < 0.5, x["vs_phase"]) for prev, x in zip(rows, rows[1:])]
    candidates = {"missed most targets last session": core.flag_effect(
        [v for f, v in after_miss if f], [v for f, v in after_miss if not f])}
    # The self-grade's backtest: the call each session would get with the votes found here.
    # In-sample, so it flatters the rule; the recorded calls (calls.csv) are the real test.
    voting = [a for a, e in by_area.items() if e["votes"]]
    # Reasons to go hard, judged the other way round: days with the reason against days without.
    by_reason = {}
    for k in ("check-in", "last session"):
        has = [x for x in rows if k in x["call"]["hard_reasons"]]
        by_reason[k] = core.flag_effect([x["vs_phase"] for x in has if x["call"]["hard_reasons"][k][0]],
                                        [x["vs_phase"] for x in has if not x["call"]["hard_reasons"][k][0]])
    hard = [k for k, e in by_reason.items() if e["supports"]]
    for x in rows:
        c = x["call"]
        why = next((w for k, (on, w) in c["hard_reasons"].items() if on and k in hard), None)
        x["verdict"] = core.evidence_call(x["levels"], voting, c["forced_rest"], c["holds"], why)
    by_verdict = {}
    for v in ("hard", "normal", "light", "rest"):
        got = [x for x in rows if x["verdict"] == v]
        if got:
            by_verdict[v] = {"n": len(got), "hit": st.mean(x["hit"] for x in got),
                             "vs_phase": st.mean(x["vs_phase"] for x in got)}
    return {"start": start, "end": end, "sessions": len(rows), "phase_days": PHASE_DAYS,
            "areas": by_area, "voting": voting,
            "hard_reasons": by_reason, "hard": hard, "candidates": candidates, "verdicts": by_verdict}

def training_plan(con, day, verdict, checkin=None, min_sets=LIFT_STREAK_MIN_SETS, training=None):
    """Which session is next and a target for each exercise. Morning decision: only sessions
    before `day` count, the same as readiness.

    Next day type follows the last session's in the rotation; the version is the other one from
    the last session of that type. The exercises are those of the last session of the same
    version (so a swap like reverse lunges carries forward), and each target is
    core.progression on the last time that exercise was done, whichever day it was on.
    A 'light' verdict holds every load; a sore or tired back flags the spine-loading lifts
    (axial_score 3). `training` is the person's split; None means this config's."""
    training = training or TRAINING
    sessions = []
    for r in con.execute("""SELECT date, muscle_groups FROM lift_session
                            WHERE date < ? AND n_sets >= ? ORDER BY date DESC LIMIT 30""",
                         (day, min_sets)):
        kind = day_type(r["muscle_groups"], training)
        if kind in training.rotation:
            exs = _session_exercises(con, r["date"])
            sessions.append({"date": r["date"], "kind": kind, "exercises": exs,
                             "variant": _variant(kind, exs, training)[0]})
    if not sessions:
        return None
    last = sessions[0]
    rotation = training.rotation
    kind = rotation[(rotation.index(last["kind"]) + 1) % len(rotation)]
    same = [x for x in sessions if x["kind"] == kind]
    names = [n for n, _ in training.variants[kind]]
    prev_variant = same[0]["variant"] if same else None
    variant = names[(names.index(prev_variant) + 1) % len(names)] if prev_variant in names else names[0]
    template = next((x for x in same if x["variant"] == variant), same[0] if same else None)
    if template is None:
        return {"kind": kind, "variant": variant, "after": last, "template": None, "exercises": []}

    mains = dict(training.variants[kind])[variant]
    order = sorted(template["exercises"],
                   key=lambda e: (e not in mains, -axial_score(e), template["exercises"].index(e)))
    back = (checkin["back"] if checkin and checkin["back_reported"] else None) or "ok"
    items = []
    for ex in order:
        when, sets = _last_sets(con, ex, day)
        p = core.progression(ex, sets, training.high_rep, training.rep_range, training.high_rep_range)
        if p is None:
            continue
        item = {"exercise": ex, "main": ex in mains, "last_date": when,
                "last": ", ".join(f"{w:g} × {r}{core.rep_unit(ex)}" if w else f"{r}" for w, r, _ in sets), **p}
        if back in ("sore", "tired") and axial_score(ex) >= 3:
            item["adjust"] = ("Back sore: skip it today." if back == "sore"
                              else "Back tired: go light, no top set.")
        items.append(item)
    # The take-it-easy rule covers the whole session, so it is said once, not on every exercise;
    # the back rules name single lifts, so they stay on those.
    return {"kind": kind, "variant": variant, "main": next((e for e in order if e in mains), None),
            "after": last, "template": template["date"], "rest": verdict == "rest", "exercises": items,
            "day_adjust": ("Take it easy: repeat last time's loads with one set fewer, and stop each "
                           "set by RPE 8." if verdict == "light" else
                           "Go hard: go for every target today." if verdict == "hard" else None)}

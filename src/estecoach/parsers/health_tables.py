"""The daily, sleep and workout table layouts, shared by both Apple Health sources.

apple_health.py (the full export.xml, weekly) and health_auto_export.py (the iPhone app's
daily JSON) feed the same tidy tables. Each table's columns, rounding and sleep logic live
here once, so the two sources can't drift apart. Stdlib only, no I/O.
"""
from collections import defaultdict

from estecoach.core import night_of, union_minutes

SUM_COLS = ["steps", "distance_mi", "active_kcal", "basal_kcal", "exercise_min",
            "stand_min", "flights", "daylight_min", "diet_kcal", "protein_g"]
AVG_COLS = ["resting_hr", "hrv_sdnn", "resp_rate", "spo2", "wrist_temp", "walking_hr",
            "hr_avg", "breathing_disturb", "physical_effort"]
# sum_method records HOW the summed columns were deduplicated across Watch and iPhone, per
# row, because the two sources do it differently and the series steps at the switch:
#   max_source    the full export: largest single-device total for the day (a lower bound)
#   apple_merged  Health Auto Export: Apple's own merge, what the Health app displays
# Averaged columns are the same readings either way.
DAILY_COLS = ["date"] + SUM_COLS + AVG_COLS + ["hr_max", "sum_method"]

SLEEP_COLS = ["night", "bedtime", "wake", "in_bed_hr", "asleep_hr", "deep_hr", "rem_hr",
              "core_hr", "awake_hr", "efficiency_pct", "n_wakes"]
ASLEEP_STAGES = ("core", "deep", "rem", "unspecified")

WORKOUT_COLS = ["date", "start", "activity", "duration_min", "active_kcal", "distance_mi",
                "hr_avg", "hr_max", "indoor", "source"]


def daily_row(day, sums, avgs, hr_max, sum_method):
    """sums: col -> total. avgs: col -> [total, n]. Falsy totals are written blank."""
    row = [day]
    for c in SUM_COLS:
        v = sums.get(c); row.append(round(v, 2) if v else "")
    for c in AVG_COLS:
        a = avgs.get(c); row.append(round(a[0] / a[1], 1) if a and a[1] else "")
    row.append(round(hr_max, 0) if hr_max else "")
    row.append(sum_method)
    return row


def sleep_rows(segments):
    """segments: (start, end, stage) with stage in ASLEEP_STAGES, 'awake' or 'inbed'.
    -> {night: row}. Nights with under an hour asleep are naps or fragments and dropped."""
    nights = defaultdict(lambda: defaultdict(list))
    for s, e, stage in segments:
        if not s or not e or e <= s: continue
        night = night_of(s).isoformat()
        if stage in ASLEEP_STAGES:
            nights[night]["asleep"].append((s, e))
        nights[night][stage].append((s, e))
    out = {}
    for night in sorted(nights):
        d = nights[night]
        allint = d["asleep"] + d["inbed"] + d["awake"]
        if not allint: continue
        start = min(s for s, _ in allint); end = max(e for _, e in allint)
        asleep = union_minutes(d["asleep"])
        inbed = union_minutes(d["inbed"]) or (end - start).total_seconds() / 60.0
        if asleep < 60: continue
        out[night] = [night, start.strftime("%H:%M"), end.strftime("%H:%M"),
                      round(inbed / 60, 2), round(asleep / 60, 2),
                      round(union_minutes(d["deep"]) / 60, 2), round(union_minutes(d["rem"]) / 60, 2),
                      round(union_minutes(d["core"]) / 60, 2), round(union_minutes(d["awake"]) / 60, 2),
                      round(100 * asleep / inbed, 1) if inbed else "",
                      len(d["awake"])]
    return out


def workout_row(start, activity, duration_min, active_kcal, distance_mi, hr_avg, hr_max,
                indoor, source):
    """start is the local 'YYYY-MM-DD HH:MM:SS ±zzzz' string both sources use."""
    f = lambda v: float(v) if v not in (None, "") else None
    r = lambda v, n: round(f(v), n) if f(v) is not None else ""
    return [start[:10], start[11:16], activity, r(duration_min, 1), r(active_kcal, 0),
            r(distance_mi, 2), r(hr_avg, 0), r(hr_max, 0), indoor, source]


# ---------------------------------------------------------------- the daily join
DAILY_COMBINED_COLS = ["date", "steps", "active_kcal", "exercise_min", "resting_hr", "hrv_sdnn", "hr_max",
                       "sleep_hr", "deep_hr", "rem_hr", "sleep_eff", "lift_sets", "lift_volume", "lift_groups"]

def write_daily_combined(tidy):
    """daily_combined.csv: daily.csv, sleep.csv and lift_sessions.csv joined on the day. Was a
    side effect of analysis/report.py, so it only changed when someone ran the report; the
    lifting-tables step writes it now, after the tables it joins."""
    import csv, os
    load = lambda n: list(csv.DictReader(open(os.path.join(tidy, n)))) if os.path.exists(os.path.join(tidy, n)) else []
    D = {r["date"]: dict(r) for r in load("daily.csv")}
    for r in load("sleep.csv"):
        D.setdefault(r["night"], {"date": r["night"]}).update(
            {"sleep_hr": r["asleep_hr"], "deep_hr": r["deep_hr"], "rem_hr": r["rem_hr"], "sleep_eff": r["efficiency_pct"]})
    for r in load("lift_sessions.csv"):
        D.setdefault(r["date"], {"date": r["date"]}).update(
            {"lift_sets": r["n_sets"], "lift_volume": r["total_volume_lb"], "lift_groups": r["muscle_groups"]})
    cols = DAILY_COMBINED_COLS
    with open(os.path.join(tidy, "daily_combined.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for k in sorted(D):
            w.writerow({**{c: "" for c in cols}, **D[k]})

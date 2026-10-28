#!/usr/bin/env python3
"""Stream Apple Health export.xml -> tidy CSVs. Stdlib only."""
import csv, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

from estebot import config
from estebot.parsers import health_tables as T

_cfg = config.load()

SRC = os.path.expanduser(sys.argv[1]) if len(sys.argv) > 1 else _cfg.newest_export("apple_health")
print(f"   health source: {SRC}")
OUT = str(_cfg.tidy)

def pdate(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z") if s else None

# metrics summed per day, deduped by taking the max single-source total
SUM_METRICS = {
    "StepCount": "steps",
    "DistanceWalkingRunning": "distance_mi",
    "ActiveEnergyBurned": "active_kcal",
    "BasalEnergyBurned": "basal_kcal",
    "AppleExerciseTime": "exercise_min",
    "AppleStandTime": "stand_min",
    "FlightsClimbed": "flights",
    "TimeInDaylight": "daylight_min",
    "DietaryEnergyConsumed": "diet_kcal",
    "DietaryProtein": "protein_g",
}
# metrics averaged per day
AVG_METRICS = {
    "RestingHeartRate": "resting_hr",
    "HeartRateVariabilitySDNN": "hrv_sdnn",
    "RespiratoryRate": "resp_rate",
    "OxygenSaturation": "spo2",
    "AppleSleepingWristTemperature": "wrist_temp",
    "WalkingHeartRateAverage": "walking_hr",
    "HeartRate": "hr_avg",
    "AppleSleepingBreathingDisturbances": "breathing_disturb",
    "PhysicalEffort": "physical_effort",
}

sums = defaultdict(float)      # (day, metric, source) -> total
avgs = defaultdict(lambda: [0.0, 0])   # (day, metric) -> [total, n]
hr_max = {}                    # day -> max HR
sleep_rows = []                # (start, end, value, source)
body = []                      # (datetime, lb, source)
workouts = []

cur_w = None
depth = 0

ctx = ET.iterparse(SRC, events=("start", "end"))
_, root = next(ctx)
depth = 1
n = 0

for ev, el in ctx:
    if ev == "start":
        depth += 1
        continue
    depth -= 1
    tag = el.tag

    if tag == "Record":
        rtype = el.get("type", "")
        short = rtype.replace("HKQuantityTypeIdentifier", "").replace("HKCategoryTypeIdentifier", "")
        sd = el.get("startDate")
        src = el.get("sourceName", "")
        if short == "SleepAnalysis":
            sleep_rows.append((sd, el.get("endDate"), el.get("value", ""), src))
        elif short == "BodyMass":
            body.append((sd, el.get("value"), el.get("unit"), src))
        elif short in SUM_METRICS:
            try:
                v = float(el.get("value"))
            except (TypeError, ValueError):
                v = 0.0
            sums[(sd[:10], SUM_METRICS[short], src)] += v
        elif short in AVG_METRICS:
            try:
                v = float(el.get("value"))
            except (TypeError, ValueError):
                v = None
            if v is not None:
                a = avgs[(sd[:10], AVG_METRICS[short])]
                a[0] += v; a[1] += 1
                if short == "HeartRate":
                    d = sd[:10]
                    if v > hr_max.get(d, 0):
                        hr_max[d] = v

    elif tag == "Workout":
        w = {
            "activity": el.get("workoutActivityType", "").replace("HKWorkoutActivityType", ""),
            "start": el.get("startDate"),
            "end": el.get("endDate"),
            "duration_min": el.get("duration"),
            "source": el.get("sourceName", ""),
        }
        for st in el.findall("WorkoutStatistics"):
            t = st.get("type", "").replace("HKQuantityTypeIdentifier", "")
            if t == "ActiveEnergyBurned":
                w["active_kcal"] = st.get("sum")
            elif t == "DistanceWalkingRunning":
                w["distance_mi"] = st.get("sum")
            elif t == "HeartRate":
                w["hr_avg"] = st.get("average"); w["hr_max"] = st.get("maximum")
        for m in el.findall("MetadataEntry"):
            if m.get("key") == "HKIndoorWorkout":
                w["indoor"] = m.get("value")
        workouts.append(w)

    if depth == 1:
        root.clear()
        n += 1
        if n % 250000 == 0:
            print(f"  ...{n:,} top-level elements", flush=True)

print(f"parsed {n:,} elements", flush=True)

# ---------- daily ----------
# dedupe multi-source sums by taking the largest single-source total for the day
best = defaultdict(float)
for (day, metric, src), v in sums.items():
    if v > best[(day, metric)]:
        best[(day, metric)] = v

days = sorted({d for d, _ in best} | {d for d, _ in avgs})
with open(f"{OUT}/daily.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(T.DAILY_COLS)
    for d in days:
        w.writerow(T.daily_row(d, {m: best[(d, m)] for m in T.SUM_COLS if (d, m) in best},
                               {m: avgs[(d, m)] for m in T.AVG_COLS if (d, m) in avgs},
                               hr_max.get(d), "max_source"))
print(f"daily.csv: {len(days)} days", flush=True)

# ---------- workouts ----------
with open(f"{OUT}/workouts.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(T.WORKOUT_COLS)
    for x in sorted(workouts, key=lambda r: r["start"] or ""):
        w.writerow(T.workout_row(x["start"], x["activity"], x.get("duration_min"), x.get("active_kcal"),
                                 x.get("distance_mi"), x.get("hr_avg"), x.get("hr_max"),
                                 x.get("indoor", ""), x["source"]))
print(f"workouts.csv: {len(workouts)} workouts", flush=True)

# ---------- sleep ----------
STAGE = {"HKCategoryValueSleepAnalysisAsleepCore": "core",
         "HKCategoryValueSleepAnalysisAsleepDeep": "deep",
         "HKCategoryValueSleepAnalysisAsleepREM": "rem",
         "HKCategoryValueSleepAnalysisAsleepUnspecified": "unspecified"}

def _stage(val):
    if val in STAGE: return STAGE[val]
    if val.endswith("Awake"): return "awake"
    if val.endswith("InBed"): return "inbed"
    return None

segs = [(pdate(sd), pdate(ed), _stage(val)) for sd, ed, val, src in sleep_rows]
nights = T.sleep_rows([x for x in segs if x[2]])
with open(f"{OUT}/sleep.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(T.SLEEP_COLS)
    for night in sorted(nights): w.writerow(nights[night])
print(f"sleep.csv: {len(nights)} nights", flush=True)

# ---------- body mass ----------
with open(f"{OUT}/body_mass.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "weight_lb", "source"])
    for sd, v, unit, src in sorted(body):
        w.writerow([sd[:10], v, src])
print(f"body_mass.csv: {len(body)} records", flush=True)

# ---------- daily feed overlay ----------
# The phone app's files replace the rows they cover from their start date. Running it here
# means the overlay wins whichever parser ran last.
from estebot.parsers import health_auto_export
health_auto_export.apply(_cfg)

#!/usr/bin/env python3
"""Health Auto Export (iPhone app) daily JSON -> overlay on the Apple Health tidy tables.

Apple Health has no server API, so the phone pushes: the app writes one JSON file per day
to iCloud Drive (Summarize Data OFF, Export Version 2). This module reads every file and
replaces the rows of daily.csv, sleep.csv, body_mass.csv and workouts.csv for the dates the
feed covers, from `from_date` on. Older rows, and any day the feed missed, keep the full
export's values. apple_health.py calls apply() as its last step, so the overlay always wins
no matter which runs last.

Every day is rebuilt from all files on each run, because a night's sleep and that
evening's wrist temperature arrive in the NEXT day's file. A day completes itself once the
following file exists.

The summed columns change method here (sum_method = apple_merged): the app sends Apple's
merged per-interval totals with no device attribution, which is what the Health app shows.
The full export's max-single-device rule undercounts by comparison (about 12% fewer steps
on a day checked both ways, both cut at the same moment). Averaged columns matched the
full export exactly on the same check.
"""
import csv, glob, json, os, subprocess, time
from collections import defaultdict
from datetime import datetime

from estebot.parsers import health_tables as T

# metric -> (column, expected units). Anything else in the file is ignored.
SUMS = {
    "step_count": ("steps", "count"),
    "walking_running_distance": ("distance_mi", "mi"),
    "active_energy": ("active_kcal", "kcal"),
    "basal_energy_burned": ("basal_kcal", "kcal"),
    "apple_exercise_time": ("exercise_min", "min"),
    "apple_stand_time": ("stand_min", "min"),
    "flights_climbed": ("flights", "count"),
    "time_in_daylight": ("daylight_min", "min"),
    "dietary_energy": ("diet_kcal", "kcal"),
    "protein": ("protein_g", "g"),
}
AVGS = {
    "resting_heart_rate": ("resting_hr", "count/min"),
    "heart_rate_variability": ("hrv_sdnn", "ms"),
    "respiratory_rate": ("resp_rate", "count/min"),
    "blood_oxygen_saturation": ("spo2", "%"),
    "apple_sleeping_wrist_temperature": ("wrist_temp", "degF"),
    "walking_heart_rate_average": ("walking_hr", "count/min"),
    "heart_rate": ("hr_avg", "count/min"),
    "breathing_disturbances": ("breathing_disturb", "count"),
    "physical_effort": ("physical_effort", "kcal/hr·kg"),
}
WEIGHT = ("weight_body_mass", "lb")
SLEEP_STAGE = {"core": "core", "deep": "deep", "rem": "rem", "awake": "awake",
               "in bed": "inbed", "inbed": "inbed", "asleep": "unspecified",
               "unspecified": "unspecified"}


class UnitError(ValueError):
    pass


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def _check_units(name, got, want):
    # A changed unit preference in the app would otherwise be converted silently wrong.
    if got != want:
        raise UnitError(f"{name}: file says {got!r}, expected {want!r}. "
                        "Fix the unit in the app (Settings) or update the mapping.")


# ---------------------------------------------------------------- parse (pure)
def parse_metrics(docs):
    """docs: parsed metric JSON files. -> (days, segments, weights)
    days: day -> (sums, avgs, hr_max). segments: (start, end, stage). weights: day -> [(time, lb, src)].
    Records are deduplicated across files on their full content."""
    seen = set()
    sums = defaultdict(lambda: defaultdict(float))
    avgs = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    hr_max = {}
    segments, weights = [], defaultdict(list)
    for doc in docs:
        for m in doc.get("data", {}).get("metrics", []):
            name, units = m.get("name"), m.get("units")
            for x in m.get("data", []):
                key = (name, json.dumps(x, sort_keys=True))
                if key in seen: continue
                seen.add(key)
                day = x["date"][:10]
                if name in SUMS:
                    col, want = SUMS[name]; _check_units(name, units, want)
                    sums[day][col] += float(x.get("qty") or 0)
                elif name in AVGS:
                    col, want = AVGS[name]; _check_units(name, units, want)
                    v = x.get("Avg") if name == "heart_rate" else x.get("qty")
                    if v is None: continue
                    v = float(v) / 100 if name == "blood_oxygen_saturation" else float(v)
                    a = avgs[day][col]; a[0] += v; a[1] += 1
                    if name == "heart_rate" and x.get("Max") is not None:
                        hr_max[day] = max(hr_max.get(day, 0), float(x["Max"]))
                elif name == "sleep_analysis":
                    stage = SLEEP_STAGE.get(str(x.get("value", "")).strip().lower())
                    if stage:
                        segments.append((ts(x["start"]), ts(x["end"]), stage))
                elif name == WEIGHT[0]:
                    _check_units(name, units, WEIGHT[1])
                    weights[day].append((x["date"], float(x["qty"]), x.get("source", "")))
    days = {d: (dict(sums.get(d, {})), dict(avgs.get(d, {})), hr_max.get(d))
            for d in set(sums) | set(avgs)}
    return days, segments, dict(weights)


def _activity(w):
    acts = w.get("activities") or []
    t = acts[0].get("activityType") if acts else None
    if t:   # 'traditional_strength_training' / 'walking' -> HK's names, as the full export uses
        return "".join(p[:1].upper() + p[1:] for p in t.split("_"))
    return (w.get("name") or "Other").replace("Indoor ", "").replace("Outdoor ", "").replace(" ", "")

def _metric(w, key):
    acts = w.get("activities") or []
    v = (acts[0].get("metrics", {}).get(key) or {}) if acts else {}
    return v.get("qty")

def parse_workouts(docs):
    """-> list of (start, workout_row), deduplicated on workout id."""
    out, seen = [], set()
    for doc in docs:
        for w in doc.get("data", {}).get("workouts", []):
            if w.get("id") in seen: continue
            seen.add(w.get("id"))
            dist = w.get("distance") or {}
            mi = dist.get("qty") if dist.get("units") == "mi" else None
            if mi is None and _metric(w, "distance") is not None:
                mi = float(_metric(w, "distance")) / 1609.344      # activity metrics are in m
            indoor = w.get("isIndoor")
            if indoor is None:     # only some workout types carry isIndoor
                loc = ((w.get("activities") or [{}])[0].get("configuration") or {}).get("locationType")
                indoor = {"indoor": True, "outdoor": False}.get(loc)
            src = w.get("source")
            out.append((w["start"], T.workout_row(
                w["start"], _activity(w),
                float(w["duration"]) / 60 if w.get("duration") is not None else None,
                (w.get("activeEnergyBurned") or {}).get("qty"), mi,
                _metric(w, "heartRateAverage"), _metric(w, "heartRateMaximum"),
                "" if indoor is None else ("1" if indoor else "0"),
                src.get("name", "") if isinstance(src, dict) else (src or ""))))
    return sorted(out, key=lambda t: t[0])


# ---------------------------------------------------------------- overlay
SF_DATALESS = 0x40000000   # macOS st_flags bit: iCloud file not downloaded (stat.SF_DATALESS, 3.13+)


class NotDownloaded(OSError):
    pass


def _is_dataless(p):
    return bool(getattr(os.stat(p), "st_flags", 0) & SF_DATALESS)


def _ensure_local(p, timeout=60, is_dataless=_is_dataless, download=None, sleep=time.sleep):
    """iCloud can evict a feed file to a placeholder; reading one fails with EDEADLK
    (13 Oct 2026). Ask iCloud for it and wait. Skipping it instead is wrong: the file holds
    the previous night's sleep and late records for the previous day, so the overlay would
    replace that day with partial values."""
    if not is_dataless(p):
        return
    (download or (lambda q: subprocess.run(["brctl", "download", q], check=False)))(p)
    for _ in range(int(timeout)):
        if not is_dataless(p):
            return
        sleep(1)
    raise NotDownloaded(f"{p}: still an iCloud placeholder after {timeout}s. "
                        "Open the folder in Finder to download it, then rerun.")


def _load(pattern):
    docs = []
    for p in sorted(glob.glob(os.path.expanduser(pattern))):
        _ensure_local(p)
        with open(p) as f:
            docs.append(json.load(f))
    return docs

def _rewrite(path, header, keep, new_rows, key=lambda r: r[0]):
    """Keep existing rows where keep(row) is true, add new_rows, sort by key, write."""
    old = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rd = csv.reader(f); old_header = next(rd, None)
            for r in rd:
                d = dict(zip(old_header, r))
                old.append([d.get(c, "") for c in header])
    rows = [r for r in old if keep(r)] + list(new_rows)
    rows.sort(key=key)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return len(new_rows)

def apply(cfg):
    src = cfg.sources.get("health_auto_export")
    if not src:
        return
    start = src["from_date"]
    mdocs, wdocs = _load(src["metrics_glob"]), _load(src.get("workouts_glob", ""))
    if not mdocs and not wdocs:
        print("   health auto export: no files found"); return
    days, segments, weights = parse_metrics(mdocs)
    tidy = cfg.tidy

    dd = {d for d in days if d >= start}
    n = _rewrite(tidy / "daily.csv", T.DAILY_COLS, lambda r: r[0] not in dd,
                 [T.daily_row(d, *days[d], "apple_merged") for d in sorted(dd)])
    nights = {k: v for k, v in T.sleep_rows(segments).items() if k >= start}
    s = _rewrite(tidy / "sleep.csv", T.SLEEP_COLS, lambda r: r[0] not in nights,
                 [nights[k] for k in sorted(nights)])
    wd = {d for d in weights if d >= start}
    b = _rewrite(tidy / "body_mass.csv", ["date", "weight_lb", "source"], lambda r: r[0] not in wd,
                 [[d, f"{round(lb, 3)}", srcname] for d in sorted(wd)
                  for _, lb, srcname in sorted(weights[d])])
    wk = [(st_, row) for st_, row in parse_workouts(wdocs) if row[0] >= start]
    wkd = {row[0] for _, row in wk}
    k = _rewrite(tidy / "workouts.csv", T.WORKOUT_COLS, lambda r: r[0] not in wkd,
                 [row for _, row in wk], key=lambda r: (r[0], r[1]))
    print(f"   health auto export: {len(mdocs)}+{len(wdocs)} files from {start}: "
          f"{n} days, {s} nights, {b} weigh-ins, {k} workouts")


def main():
    from estebot import config
    apply(config.load())


if __name__ == "__main__":
    main()

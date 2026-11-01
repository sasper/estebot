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
import csv, glob, hashlib, json, os, pickle, re, subprocess, time
from array import array
from collections import defaultdict
from datetime import date, datetime, timedelta

from estecoach.parsers import health_tables as T

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
COLS = [c for c, _ in SUMS.values()] + [c for c, _ in AVGS.values()]
_COL = {name: i for i, name in enumerate(list(SUMS) + list(AVGS))}
_NSUMS = len(SUMS)
NAN = float("nan")


class Rec:
    """A record as it's decoded: its content digest plus the date, qty and source, or the whole
    dict when it carries more than that (heart rate, sleep, weight). A metric therefore never
    exists as a list of full dicts: 155,000 basal-energy records as dicts were most of the
    274 MB one 52 MB file took (1 Nov 2026, OOM kills on the 512 MB server)."""
    __slots__ = ("d", "date", "qty", "source", "keys", "x")

    def as_dict(self):
        if self.x is not None: return self.x
        v = {"date": self.date, "qty": self.qty, "source": self.source}
        return {k: v[k] for k in self.keys}


def _digest(x):
    return hashlib.blake2b(json.dumps(x, sort_keys=True).encode(), digest_size=16).digest()

_FLAT = {"date", "qty", "source"}

def _record(x, memo):
    r = Rec()
    r.d = _digest(x)
    if x.keys() <= _FLAT:
        r.x, r.date, r.qty = None, x["date"], x.get("qty")
        src, keys = x.get("source", ""), tuple(x)
        r.source, r.keys = memo.setdefault(src, src), memo.setdefault(keys, keys)
    else:
        r.x, r.date, r.qty, r.source, r.keys = x, x["date"], x.get("qty"), None, None
    return r


class FileParse:
    """One metric file reduced to what the tables need, grouped by the record's own day:
    day -> (keys, cols, values, values2, segments, weights). keys are 64-bit hashes of each
    record's metric name and full content. Identical records carry the same date, so
    deduplicating within a day is exactly deduplicating across everything."""
    def __init__(self):
        self.days, self._memo = {}, {}

    def _bucket(self, day):
        b = self.days.get(day)
        if b is None:
            b = self.days[day] = (array("Q"), array("B"), array("d"), array("d"), [], [])
        return b

    def add_metric(self, name, units, data):
        """data: records (dicts or Recs) of one metric, possibly a generator; read to the end
        only when the metric is one the tables use."""
        want = (SUMS.get(name) or AVGS.get(name) or (WEIGHT if name == WEIGHT[0] else None))
        if want is None and name != "sleep_analysis":
            return
        col, prefix, checked = _COL.get(name), f"{name}\0".encode(), want is None
        for r in data:
            if not checked:
                _check_units(name, units, want[1]); checked = True
            if not isinstance(r, Rec): r = _record(r, self._memo)
            key = int.from_bytes(hashlib.blake2b(prefix + r.d, digest_size=8).digest())
            x, date, qty = r.x, r.date, r.qty
            day = date[:10]
            if col is not None and col < _NSUMS:
                b = self._bucket(day)
                b[0].append(key); b[1].append(col); b[2].append(float(qty or 0)); b[3].append(NAN)
            elif col is not None:
                hr = name == "heart_rate"
                v = (x or {}).get("Avg") if hr else qty
                if v is None: continue      # contributes nothing; its copies are skipped too
                v = float(v) / 100 if name == "blood_oxygen_saturation" else float(v)
                mx = x.get("Max") if hr else None
                b = self._bucket(day)
                b[0].append(key); b[1].append(col); b[2].append(v)
                b[3].append(NAN if mx is None else float(mx))
            elif name == "sleep_analysis":
                x = r.as_dict()
                stage = SLEEP_STAGE.get(str(x.get("value", "")).strip().lower())
                if stage:
                    self._bucket(day)[4].append((key, x["start"], x["end"], stage))
            else:
                x = r.as_dict()
                self._bucket(day)[5].append((key, x["date"], float(x["qty"]), x.get("source", "")))

    def __getstate__(self):
        return self.days

    def __setstate__(self, days):
        self.days, self._memo = days, {}

    @classmethod
    def of_doc(cls, doc):
        fp = cls()
        for m in doc.get("data", {}).get("metrics", []):
            fp.add_metric(m.get("name"), m.get("units"), m.get("data", []))
        return fp

    @classmethod
    def of_file(cls, path):
        """Streams the file a megabyte at a time. json.load held the whole text, and one
        non-ASCII character (the "·" in physical effort's unit) makes Python store every
        character in two bytes: 100 MB of string for a 52 MB file before any decoding."""
        fp = cls()
        with open(path, encoding="utf-8") as f:
            st = _Stream(f)
            for k in st.members():
                if k != "data": st.skip(); continue
                for k2 in st.members():
                    if k2 != "metrics": st.skip(); continue
                    for _ in st.elements():
                        fp._metric(st)
        return fp

    def _metric(self, st):
        """One metric object. Its keys come in any order ("data" before "name" in some), so
        records seen before the name and units are held as Recs until they arrive."""
        name = units = pending = None
        for k in st.members():
            if k == "name": name = st.value()
            elif k == "units": units = st.value()
            elif k != "data": st.skip()
            elif name is not None and units is not None:
                if name in _COL or name in ("sleep_analysis", WEIGHT[0]):
                    self.add_metric(name, units, (st.value() for _ in st.elements()))
                else:
                    st.skip()
            else:
                pending = [_record(st.value(), self._memo) for _ in st.elements()]
        if pending:
            self.add_metric(name, units, pending)


_WS = re.compile(r"[ \t\n\r]*")
_DEC = json.JSONDecoder()


class _Stream:
    """Just enough of a pull parser for the feed's shape: walk objects and arrays, and
    decode each record whole with the stdlib decoder, refilling the buffer as needed."""
    def __init__(self, f, chunk=1 << 20):
        self.f, self.chunk, self.buf, self.pos, self.eof = f, chunk, "", 0, False

    def _more(self):
        if self.eof: return False
        s = self.f.read(self.chunk)
        if not s:
            self.eof = True; return False
        self.buf, self.pos = self.buf[self.pos:] + s, 0
        return True

    def peek(self):
        while True:
            self.pos = _WS.match(self.buf, self.pos).end()
            if self.pos < len(self.buf): return self.buf[self.pos]
            if not self._more(): raise ValueError("feed file ends mid-JSON")

    def _take(self, ok):
        c = self.peek()
        if c not in ok:
            raise ValueError(f"feed file: expected one of {ok!r}, found {c!r}")
        self.pos += 1
        return c

    def value(self):
        self.peek()
        while True:
            try:
                v, end = _DEC.raw_decode(self.buf, self.pos)
            except json.JSONDecodeError:
                if self._more(): continue
                raise
            if end == len(self.buf) and self._more():   # a number may go on in the next chunk
                continue
            self.pos = end
            return v

    def skip(self):
        """Pass over a value without keeping it, however large."""
        c = self.peek()
        if c == "{":
            for _ in self.members(): self.skip()
        elif c == "[":
            for _ in self.elements(): self.skip()
        else:
            self.value()

    def members(self):
        """Yield each key of an object; the caller reads or skips its value before the next."""
        self._take("{")
        if self.peek() == "}":
            self.pos += 1; return
        while True:
            key = self.value()
            self._take(":")
            yield key
            if self._take(",}") == "}": return

    def elements(self):
        """Yield once per array element; the caller reads or skips it before the next."""
        self._take("[")
        if self.peek() == "]":
            self.pos += 1; return
        while True:
            yield
            if self._take(",]") == "]": return


def combine(parses):
    """FileParses in file order -> (days, segments, weights), the shape parse_metrics returns.
    Per (day, column) the values add in file then record order, as they always have, so the
    sums are bit-for-bit what reading every file in sequence gives."""
    sums = defaultdict(lambda: defaultdict(float))
    avgs = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    hr_max = {}
    segments, weights = [], defaultdict(list)
    for day in sorted(set().union(*(fp.days for fp in parses))):
        seen = set()
        for fp in parses:
            b = fp.days.get(day)
            if b is None: continue
            for key, c, v, v2 in zip(*b[:4]):
                if key in seen: continue
                seen.add(key)
                if c < _NSUMS:
                    sums[day][COLS[c]] += v
                else:
                    a = avgs[day][COLS[c]]; a[0] += v; a[1] += 1
                    if v2 == v2:
                        hr_max[day] = max(hr_max.get(day, 0), v2)
            for key, s, e, stage in b[4]:
                if key in seen: continue
                seen.add(key)
                segments.append((ts(s), ts(e), stage))
            for key, *w in b[5]:
                if key in seen: continue
                seen.add(key)
                weights[day].append(tuple(w))
    days = {d: (dict(sums.get(d, {})), dict(avgs.get(d, {})), hr_max.get(d))
            for d in set(sums) | set(avgs)}
    return days, segments, dict(weights)


def parse_metrics(docs):
    """docs: parsed metric JSON files. -> (days, segments, weights)
    days: day -> (sums, avgs, hr_max). segments: (start, end, stage). weights: day -> [(time, lb, src)].
    Records are deduplicated across files on their full content, remembered as a 64-bit
    hash. Two different records sharing a hash would drop one of them; at a few million
    records a year that's about one chance in a million."""
    return combine([FileParse.of_doc(d) for d in docs])


# ---------------------------------------------------------------- workouts
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


CACHE_DIR = "raw/health_auto_export/cache"     # under data_dir; raw/ is never committed

def _paths(pattern):
    return sorted(glob.glob(os.path.expanduser(pattern))) if pattern else []

def _docs(paths):
    """Workout files, parsed whole one at a time (a few KB each)."""
    for p in paths:
        _ensure_local(p)
        with open(p) as f:
            yield json.load(f)

# What a cached parse depends on besides the file: bump CACHE_VERSION when FileParse changes.
CACHE_VERSION = 1
_SIG = hashlib.blake2b(repr((CACHE_VERSION, SUMS, AVGS, WEIGHT, SLEEP_STAGE)).encode(),
                       digest_size=8).hexdigest()

def _parsed(path, cache_dir):
    """FileParse of one metric file, from cache_dir when the file hasn't changed since. A
    phone upload used to re-read every file still on disk; now only the one that changed is
    read. Keyed on size and mtime (an upload replaces the file) plus _SIG."""
    st = os.stat(path)
    tag = (_SIG, st.st_size, st.st_mtime_ns)
    c = cache_dir / (os.path.basename(path) + ".pickle")
    try:
        with open(c, "rb") as f:
            got, fp = pickle.load(f)
        if got == tag:
            return fp
    except (OSError, EOFError, ValueError, TypeError, pickle.UnpicklingError):
        pass
    _ensure_local(path)
    fp = FileParse.of_file(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = c.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump((tag, fp), f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(c)
    return fp

def _metric_parses(paths, cache_dir):
    keep = {os.path.basename(p) + ".pickle" for p in paths}
    if cache_dir.is_dir():
        for c in cache_dir.glob("*.pickle"):      # the feed prunes old files; their caches go too
            if c.name not in keep: c.unlink()
    return [_parsed(p, cache_dir) for p in paths]

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

# ---------------------------------------------------------------- medications
MED_COLS = ["date", "time", "medication", "dose", "unit", "status", "scheduled", "scheduled_dose"]

def parse_medications(docs):
    """Medications export (Export Version 2) -> dose rows, one per logged dose. The app sends
    no id, so a dose is deduplicated on its whole content; files seen so far hold only their
    own day's doses, but a resend must not count twice. scheduled = 1 when the dose was
    against a schedule (it carries scheduledDate), 0 for one taken as needed."""
    seen, rows = set(), []
    for doc in docs:
        for m in doc.get("data", {}).get("medications", []):
            key = json.dumps(m, sort_keys=True)
            if key in seen or not m.get("start"):
                continue
            seen.add(key)
            when = m["start"]
            rows.append([when[:10], when[11:16], m.get("displayText") or "", _num(m.get("dosage")),
                         m.get("units") or "", m.get("status") or "",
                         "1" if m.get("scheduledDate") else "0", _num(m.get("scheduledDosage"))])
    return sorted(rows)

def _num(v):
    return "" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)

def _file_days(paths):
    """The day each feed file is named for (HealthAutoExport-YYYY-MM-DD.json)."""
    return {os.path.basename(p)[len("HealthAutoExport-"):-len(".json")] for p in paths}

def apply(cfg):
    src = cfg.sources.get("health_auto_export")
    if not src:
        return
    start = src["from_date"]
    mpaths, wpaths = _paths(src["metrics_glob"]), _paths(src.get("workouts_glob", ""))
    dpaths = _paths(src.get("medications_glob", ""))
    if dpaths:
        doses = [r for r in parse_medications(_docs(dpaths)) if r[0] >= start]
        mine = {d for d in _file_days(dpaths) if d >= start}     # the same own-file rule as daily
        nd = _rewrite(cfg.tidy / "medications.csv", MED_COLS, lambda r: r[0] not in mine,
                      [r for r in doses if r[0] in mine], key=lambda r: (r[0], r[1], r[2]))
        print(f"   health auto export: {len(dpaths)} medication files: {nd} doses")
    if not mpaths and not wpaths:
        print("   health auto export: no files found"); return
    days, segments, weights = combine(_metric_parses(mpaths, cfg.data_dir / CACHE_DIR))
    tidy = cfg.tidy

    # A day is rebuilt only while its own file is here. The next day's file carries a few late
    # records for it (that evening's wrist temperature), so without this a day whose file was
    # pruned, or never uploaded, came back as those few records with steps and energy blank.
    # 27 Oct 2026 was blanked that way on the server's first sync (issue #62).
    own = _file_days(mpaths)
    dd = {d for d in days if d >= start and d in own}
    n = _rewrite(tidy / "daily.csv", T.DAILY_COLS, lambda r: r[0] not in dd,
                 [T.daily_row(d, *days[d], "apple_merged") for d in sorted(dd)])
    # A night is keyed by its evening and arrives in the next morning's file.
    nights = {k: v for k, v in T.sleep_rows(segments).items()
              if k >= start and (date.fromisoformat(k) + timedelta(days=1)).isoformat() in own}
    s = _rewrite(tidy / "sleep.csv", T.SLEEP_COLS, lambda r: r[0] not in nights,
                 [nights[k] for k in sorted(nights)])
    wd = {d for d in weights if d >= start and d in own}
    b = _rewrite(tidy / "body_mass.csv", ["date", "weight_lb", "source"], lambda r: r[0] not in wd,
                 [[d, f"{round(lb, 3)}", srcname] for d in sorted(wd)
                  for _, lb, srcname in sorted(weights[d])])
    # Workouts arrive whole (deduplicated on id), so only days before the oldest file are at risk.
    wfrom = max(start, min(_file_days(wpaths), default=start))
    wk = [(st_, row) for st_, row in parse_workouts(_docs(wpaths)) if row[0] >= wfrom]
    wkd = {row[0] for _, row in wk}
    k = _rewrite(tidy / "workouts.csv", T.WORKOUT_COLS, lambda r: r[0] not in wkd,
                 [row for _, row in wk], key=lambda r: (r[0], r[1]))
    print(f"   health auto export: {len(mpaths)}+{len(wpaths)} files from {start}: "
          f"{n} days, {s} nights, {b} weigh-ins, {k} workouts")


def main():
    from estecoach import config
    apply(config.load())


if __name__ == "__main__":
    main()

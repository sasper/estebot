"""A made-up person's data directory, for the demo. Nobody's real data goes into it.

    python -m estebot.demo --out DIR [--seed 7] [--end YYYY-MM-DD|today] [--days 180]

Writes what the parsers and the phone would have left in a data repo: tidy/*.csv in the
parsers' exact layouts, health-context.txt notes, supplements.toml, a short ledger.md with
predictions.toml, and an estebot.toml pointing data_dir at DIR. The app then runs on it
unchanged with ESTEBOT_CONFIG=DIR/estebot.toml.

The person: about six months into a moderate cut, push/pull/legs in A and B versions, RPE
logged, incline walks on some rest days, two short illnesses, a work trip, two deload weeks
and one tweaked lower back. Every value comes from one random.Random(seed), so the same seed
and end date give byte-identical files. The default end date is fixed for the same reason;
the hosted demo passes --end to roll forward.
"""
import argparse, csv, math, random, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from estebot import core
from estebot.parsers import health_tables as T, lift_tables as L

DEFAULT_END = date(2026, 10, 24)
MARKER = ".estebot-demo"        # a directory without it is never written into unless empty

# Starting weight, height and age of the made-up person. Generic numbers.
START_LB, HEIGHT_CM, AGE = 214.0, 178, 36

# name: (muscle group as jefit.py's GROUPS assigns it, kind, true 1RM at the start in lb,
#        gain over the cut as a fraction). kind: main = 3 x 5 after two warm-ups,
#        acc = 3 sets in core.progression's rep range, bw = bodyweight (logged at 0 lb).
EXERCISES = {
    "Barbell Bench Press":            ("chest", "main", 225, 0.05),
    "Barbell Military Press":         ("shoulders", "main", 140, 0.04),
    "Barbell Bent-Over Row":          ("back", "main", 205, 0.05),
    "Barbell Deadlift":               ("hamstrings", "main", 365, 0.06),
    "Barbell Squat":                  ("quads", "main", 295, 0.06),
    "Barbell Front Squat":            ("quads", "main", 235, 0.05),
    "Dumbbell Incline Bench Press":   ("chest", "acc", 85, 0.05),
    "Dumbbell Bench Press":           ("chest", "acc", 95, 0.05),
    "Dumbbell Lateral Raise":         ("shoulders", "acc", 45, 0.04),
    "Cable Tricep Pushdown":          ("triceps", "acc", 95, 0.06),
    "Cable Overhead Tricep Extension": ("triceps", "acc", 85, 0.06),
    "Lat Pulldown":                   ("back", "acc", 210, 0.05),
    "Face Pull":                      ("back", "acc", 85, 0.05),
    "Dumbbell Hammer Curl":           ("biceps", "acc", 55, 0.04),
    "Pull-Up":                        ("back", "bw", 268, -0.06),   # body weight + 1RM
    "Dumbbell Row":                   ("back", "acc", 115, 0.05),
    "Barbell Curl":                   ("biceps", "acc", 115, 0.04),
    "Romanian Deadlift":              ("hamstrings", "acc", 285, 0.06),
    "Leg Press":                      ("quads", "acc", 520, 0.06),
    "Seated Leg Curl":                ("hamstrings", "acc", 170, 0.05),
    "Standing Calf Raise":            ("calves", "acc", 320, 0.05),
    "Barbell Hip Thrust":             ("hamstrings", "acc", 345, 0.07),
    "Machine Leg Extension":          ("quads", "acc", 210, 0.05),
    "Seated Calf Raise":              ("calves", "acc", 230, 0.05),
}

# The rotation as engine.ROTATION and engine.VARIANTS read it: the main lift tells A from B.
SESSIONS = {
    ("push", "A"): ["Barbell Bench Press", "Dumbbell Incline Bench Press", "Dumbbell Lateral Raise",
                    "Cable Tricep Pushdown"],
    ("push", "B"): ["Barbell Military Press", "Dumbbell Bench Press", "Dumbbell Lateral Raise",
                    "Cable Overhead Tricep Extension"],
    ("pull", "A"): ["Barbell Bent-Over Row", "Lat Pulldown", "Face Pull", "Dumbbell Hammer Curl"],
    ("pull", "B"): ["Barbell Deadlift", "Pull-Up", "Dumbbell Row", "Barbell Curl"],
    ("legs", "A"): ["Barbell Squat", "Romanian Deadlift", "Leg Press", "Seated Leg Curl",
                    "Standing Calf Raise"],
    ("legs", "B"): ["Barbell Front Squat", "Barbell Hip Thrust", "Machine Leg Extension",
                    "Seated Calf Raise"],
}
ROTATION = ("push", "pull", "legs")

# jefit.py's GROUPS order plus "other": the columns of weekly_volume.csv.
GROUP_COLS = ["olympic", "carry", "quads", "hamstrings", "calves", "chest", "back", "shoulders",
              "biceps", "triceps", "core", "cardio", "other"]

# Daily micronutrients from food at about 2,100 kcal, keyed by Cronometer's column names.
# Vitamin E, potassium and fiber run short and vitamin D is mostly the supplement, so the
# nutrition card has gaps and an upper-limit row to show.
MICROS = {
    "B1 (Thiamine) (mg)": 1.7, "B2 (Riboflavin) (mg)": 2.2, "B3 (Niacin) (mg)": 36,
    "B5 (Pantothenic Acid) (mg)": 6.4, "B6 (Pyridoxine) (mg)": 2.5, "B12 (Cobalamin) (µg)": 5.6,
    "Folate (µg)": 450, "Vitamin A (µg)": 880, "Vitamin C (mg)": 115, "Vitamin D (IU)": 230,
    "Vitamin E (mg)": 9.2, "Vitamin K (µg)": 150, "Calcium (mg)": 1080, "Copper (mg)": 1.25,
    "Iron (mg)": 17, "Magnesium (mg)": 365, "Manganese (mg)": 3.6, "Phosphorus (mg)": 1700,
    "Potassium (mg)": 2850, "Selenium (µg)": 125, "Zinc (mg)": 13.5,
    "Cholesterol (mg)": 420, "Omega-3 (g)": 1.4, "Omega-6 (g)": 11, "Caffeine (mg)": 190,
}
# Nutrients many foods leave blank, so their kcal coverage runs lower than the rest.
SPARSE = {"Vitamin D (IU)", "Vitamin E (mg)", "Vitamin K (µg)", "B5 (Pantothenic Acid) (mg)",
          "Manganese (mg)", "Selenium (µg)", "Omega-3 (g)", "Omega-6 (g)", "Caffeine (mg)"}

SUPPLEMENTS = """\
# The demo person's daily stack, added to food on the nutrition card.

[[product]]
name = "Vitamin D3"
servings_per_day = 1
per_serving = {{ "Vitamin D (IU)" = 2000 }}

[[product]]
name = "Magnesium glycinate"
since = "{mag_since}"
servings_per_day = 1
per_serving = {{ "Magnesium (mg)" = 200 }}

[[product]]
name = "Fish oil"
servings_per_day = 2
per_serving = {{ "Omega-3 (g)" = 0.6 }}

[notes]
"Vitamin D (IU)" = "Food alone is about 230 IU a day; the 2,000 IU capsule is most of it."
"""


def r5(x):
    return max(5.0, 5.0 * round(x / 5))


class Lifter:
    """Each exercise's working weight and rep target, moved on the way people actually run a
    double progression: add load when the top set was easy, a rep when it moved, hold when it
    ground. The sets come from a true 1RM that climbs slowly and dips on bad days, so the
    logged RPE is the gap between what was done and what was possible."""

    def __init__(self, rng):
        self.rng = rng
        self.state = {}
        for name, (_, kind, orm, _) in EXERCISES.items():
            lo, _hi = self.rep_range(name)
            if kind == "main":
                self.state[name] = {"w": r5(orm / 1.27), "t": 5, "fails": 0}
            elif kind == "acc":
                self.state[name] = {"w": r5(orm / (1 + (lo + 3) / 30)), "t": lo, "fails": 0}
            else:
                self.state[name] = {"w": 0.0, "t": 0, "fails": 0}

    @staticmethod
    def rep_range(name):
        return (12, 15) if any(k in name.lower() for k in core.HIGH_REP) else (8, 12)

    def capacity(self, name, i, factor):
        _, _, orm, gain = EXERCISES[name]
        return orm * (1 + gain * (1 - math.exp(-i / 70))) * factor * (1 + self.rng.gauss(0, 0.015))

    def rpe(self, rmax, reps):
        return min(10.0, max(6.0, round((10 - (rmax - reps)) * 2) / 2))

    def logged(self, rpe):
        return rpe if self.rng.random() > 0.04 else None   # now and then it is left blank

    def session(self, names, i, factor, bw, deload=False, stop_early=None):
        """-> [(exercise, weight_lb, reps, rpe, set_type)] in the order performed."""
        out = []
        for name in names:
            kind, st = EXERCISES[name][1], self.state[name]
            m = self.capacity(name, i, factor)
            n_sets = 2 if deload else 3
            if kind == "main":
                w = r5(st["w"] * 0.85) if deload else st["w"]
                for frac, reps in ((0.5, 5), (0.7, 3)):
                    out.append((name, max(45.0, r5(w * frac)), reps, None, "warmup"))
                done = []
                for k in range(n_sets):
                    rmax = 30 * (m / w - 1) - 0.5 * k
                    reps = 5 if rmax >= 4.7 else max(1, int(rmax))
                    if stop_early == name and k == n_sets - 1:
                        reps = 2                        # the set where the back went
                    rpe = self.rpe(rmax, reps)
                    out.append((name, w, reps, self.logged(rpe), "normal")); done.append((reps, rpe))
                if not deload and stop_early != name:
                    if min(r for r, _ in done) < 5:
                        st["fails"] += 1
                        if st["fails"] >= 2:            # two misses: back off and build again
                            st["w"], st["fails"] = r5(w * 0.9), 0
                    elif done[0][1] <= 8:                # an easy first set: add load
                        st["w"], st["fails"] = w + core.load_step(name), 0
                    else:
                        st["fails"] = 0
            elif kind == "acc":
                lo, hi = self.rep_range(name)
                w = r5(st["w"] * 0.85) if deload else st["w"]
                done = []
                for k in range(n_sets):
                    rmax = 30 * (m / w - 1) - 0.6 * k
                    reps = max(1, min(st["t"], int(rmax - 1)))   # a rep or so in reserve
                    rpe = self.rpe(rmax, reps)
                    out.append((name, w, reps, self.logged(rpe), "normal")); done.append((reps, rpe))
                if not deload:                  # judged on the first set, as most people do
                    reps, rpe = done[0]
                    st["stuck"] = st.get("stuck", 0) + 1
                    if (reps >= hi and rpe <= 9) or (st["stuck"] >= 4 and reps >= hi - 2):
                        st["w"], st["t"], st["stuck"] = w + core.load_step(name), lo, 0
                    elif reps < lo - 1:
                        st["w"], st["t"] = r5(w - core.load_step(name)), lo
                    elif reps >= st["t"]:
                        st["t"], st["stuck"] = min(hi, st["t"] + 1), 0
            else:
                for k in range(n_sets):
                    rmax = 30 * (m / bw - 1) - 1.2 * k
                    reps = max(1, int(rmax - 1))
                    out.append((name, 0.0, reps, self.logged(self.rpe(rmax, reps)), "normal"))
        return out


def _events(n):
    """day index -> what is going on. Placed by fraction of the span so a short --days run
    still has every kind, and the last three weeks are ordinary training."""
    ev = {}
    def mark(frac, length, what):
        a = int(n * frac)
        for i in range(a, min(n - 21, a + length)):
            ev.setdefault(i, what)
    mark(0.36, 3, "sick")
    mark(0.74, 2, "stomach")
    mark(0.45, 4, "travel")
    mark(0.30, 7, "deload")
    mark(0.84, 7, "deload")
    return ev


def generate(out, seed=7, end=DEFAULT_END, days=180):
    rng = random.Random(seed)
    out = Path(out).resolve()
    start = end - timedelta(days=days - 1)
    iso = lambda i: (start + timedelta(days=i)).isoformat()
    ev = _events(days)
    back_from = int(days * 0.56)
    lifter = Lifter(rng)

    weights, daily, workouts, segments, sets, sess_min = [], [], [], [], [], {}
    nutrition, nutrients, notes = [], [], []
    trend, water, prev_intake, prev_sodium = START_LB, 0.0, None, 3000.0
    sleep_prev, heavy_prev, kind_i = 7.0, False, 0
    variant = {k: "B" for k in ROTATION}           # so each kind opens on A
    tweak, back_days = None, {}

    for i in range(days):
        day = start + timedelta(days=i)
        dow, what, last = day.weekday(), ev.get(i), i == days - 1
        sick = what in ("sick", "stomach")
        kg = trend / 2.20462
        basal = 10 * kg + 6.25 * HEIGHT_CM - 5 * AGE + 5 + rng.gauss(0, 15)

        # ---- morning weigh-in: trend plus water, which follows yesterday's food and salt
        water = 0.55 * water + rng.gauss(0, 0.55)
        if prev_intake is not None:
            water += 0.0008 * (prev_intake - 2150) + 0.00025 * (prev_sodium - 3000)
        if what == "stomach":
            water -= 1.0
        p_weigh = 0.0 if what == "travel" else 0.5 if sick else 0.66 if dow >= 5 else 0.9
        if i == 0 or last or rng.random() < p_weigh:
            weights.append((iso(i), round(trend + water, 1)))

        # ---- training
        factor = (0.96 if sleep_prev < 6 else 1.0) * (0.97 if back_days.get(i) else 1.0)
        session = None
        lift_day = (dow not in (3, 6) and rng.random() > 0.07 and not sick
                    and what != "travel" and back_days.get(i) not in ("sore", "tired") and not last)
        if lift_day:
            kind = ROTATION[kind_i % 3]
            v = "A" if variant[kind] == "B" else "B"
            stop = None
            if tweak is None and i >= back_from and (kind, v) == ("pull", "B"):
                tweak, stop = i, "Barbell Deadlift"
                back_days.update({i + 1: "sore", i + 2: "tired", i + 4: "ok"})
            bw = core.trailing_mean([(date.fromisoformat(d), w) for d, w in weights], day) or trend
            session = lifter.session(SESSIONS[(kind, v)], i, factor, bw,
                                     deload=what == "deload", stop_early=stop)
            variant[kind], kind_i = v, kind_i + 1
            mins = (38 if what == "deload" else 55) + rng.uniform(0, 18)
            hh = rng.choice([6, 6, 17, 18]) if dow < 5 else rng.choice([9, 10])
            start_s = f"{iso(i)} {hh:02d}:{rng.randrange(0, 50):02d}:00 -0500"
            sess_min[iso(i)] = round(mins, 1)
            workouts.append(T.workout_row(start_s, "TraditionalStrengthTraining", mins,
                                          mins * rng.uniform(5.5, 7), "", rng.gauss(112, 6),
                                          rng.gauss(152, 6), "1", "Apple Watch"))
            sets += [(iso(i), *x) for x in session]
        walk = None
        if not lift_day and not sick and not last and rng.random() < (0.7 if what == "travel" else 0.45):
            walk = rng.uniform(26, 42)
            indoor = what != "travel"
            hh = 19 if dow < 5 else 11
            workouts.append(T.workout_row(f"{iso(i)} {hh:02d}:{rng.randrange(0, 40):02d}:00 -0500",
                                          "Walking", walk, walk * rng.uniform(5.5, 6.8),
                                          walk / 60 * (2.9 if indoor else 3.3),
                                          rng.gauss(116 if indoor else 104, 4), rng.gauss(132, 5),
                                          "1" if indoor else "0", "Apple Watch"))

        # ---- the day as the watch saw it
        steps = max(800, rng.gauss(2600 if sick else 7400, 500 if sick else 1700))
        if walk: steps += walk * 105
        if what == "travel": steps += 2500
        if last: steps *= 0.35                     # the demo's "today" is a morning
        ex_min = ((sess_min[iso(i)] * rng.uniform(0.45, 0.8) if session else 0) + (walk or 0)
                  + max(0, rng.gauss(4, 3)))
        if sick: ex_min = rng.uniform(0, 3)
        if last: ex_min = rng.uniform(2, 8)
        active = 0.032 * steps * kg / 97 + (sess_min[iso(i)] * 4.2 if session else 0) + rng.gauss(140, 30)
        heavy = bool(session) and any(n in ("Barbell Squat", "Barbell Deadlift", "Barbell Front Squat")
                                      for n, *_ in session)
        hrv = 41 + 9 * (1 - math.exp(-i / 60))           # fitter as the cut goes on
        hrv *= math.exp(rng.gauss(0, 0.11))
        hrv *= (0.82 if sleep_prev < 6 else 1) * (0.9 if heavy_prev else 1)
        hrv *= 0.72 if (sick or ev.get(i - 1) in ("sick", "stomach")) else 0.92 if what == "travel" else 1
        rhr = 63 - 5 * (1 - math.exp(-i / 75)) + rng.gauss(0, 1.2)
        rhr += (3 if sleep_prev < 6 else 0) + (1.5 if heavy_prev else 0) + (6 if sick else 0)
        worn = rng.random() > 0.03
        sums = {"steps": round(steps), "distance_mi": steps / 2250, "active_kcal": active,
                "basal_kcal": basal * (0.5 if last else 1), "exercise_min": round(ex_min),
                "stand_min": rng.gauss(150 if sick else 200, 30), "flights": rng.randrange(3, 16),
                "daylight_min": max(5, rng.gauss(55, 30))}
        avgs = {"resting_hr": rhr, "hrv_sdnn": hrv, "resp_rate": rng.gauss(16.8 if sick else 15.4, 0.4),
                "spo2": min(100, rng.gauss(96.6, 0.7)), "wrist_temp": rng.gauss(36.1 if sick else 35.5, 0.15),
                "walking_hr": rng.gauss(99, 4), "hr_avg": rng.gauss(78 if sick else 73, 3),
                "breathing_disturb": max(0.5, rng.gauss(3.5, 1.2)), "physical_effort": rng.gauss(1.9, 0.3)}
        if not worn:
            avgs = {k: v for k, v in avgs.items() if k not in ("resting_hr", "hrv_sdnn", "wrist_temp")}
        hrmax = rng.gauss(155, 7) if session else rng.gauss(128, 6) if walk else rng.gauss(118, 6)
        daily.append(T.daily_row(iso(i), sums, {k: [v, 1] for k, v in avgs.items()}, hrmax, "apple_merged"))

        # ---- food. Intake sits a planned deficit under burn; the trend follows what was eaten.
        tdee = (basal + active) / 0.9                     # the watch leaves out digesting food
        pct = 0.78 - 0.26 * i / days                      # %/week, easing off as the cut goes on
        target = tdee - trend * pct / 100 * 3500 / 7
        intake = target + rng.gauss(0, 170) + (420 if dow == 5 else -60)
        if what == "travel": intake += 450
        if sick: intake = rng.gauss(1350, 150)
        sodium = rng.gauss(4100 if dow == 5 or what == "travel" else 3000, 350)
        if not last:
            trend -= (tdee - intake) / 3500
        prev_intake, prev_sodium = intake, sodium
        r = rng.random()
        status = ("partial" if last else "missing" if r < 0.04 else
                  "partial" if (r < 0.13 or sick or (what == "travel" and r < 0.6)) else "complete")
        if status != "missing":
            share = 1.0 if status == "complete" else rng.uniform(0.45, 0.7)
            kcal = intake * share
            prot = max(60, rng.gauss(0.86 * trend, 11)) * (share if status == "partial" else 1)
            fat = max(30, rng.gauss(0.31 * trend, 9) * share)
            carbs = max(40, (kcal - 4 * prot - 9 * fat) / 4)
            fiber = carbs * rng.uniform(0.12, 0.15)
            meals = rng.choice([3, 4, 4, 5]) if status == "complete" else rng.choice([1, 2])
            nutrition.append([iso(i), round(kcal, 1), round(prot, 1), round(carbs, 1), round(fat, 1),
                              round(fiber, 1), round(carbs * rng.uniform(0.22, 0.3), 1),
                              round(sodium * share, 1), round(rng.gauss(2900, 400) * share, 1),
                              meals, "true" if status == "complete" else "false"])
            eat_out = rng.random() < 0.12
            for name, amount, cov in [("Carbs (g)", carbs, 100), ("Fat (g)", fat, 100),
                                      ("Fiber (g)", fiber, 100), ("Protein (g)", prot, 100),
                                      ("Sodium (mg)", sodium * share, 100)]:
                nutrients.append([iso(i), name, round(amount, 3), cov])
            for name, base in MICROS.items():
                cov = 100.0 if not eat_out else rng.uniform(55, 78)
                if name in SPARSE: cov -= rng.uniform(2, 14)
                amount = base * kcal / 2100 * math.exp(rng.gauss(0, 0.18)) * cov / 100
                nutrients.append([iso(i), name, round(amount, 3), round(cov, 1)])

        # ---- tonight's sleep (keyed to this evening, like sleep.csv)
        if not last:
            hours = rng.gauss(7.05 + (0.35 if dow in (4, 5) else 0), 0.42)
            if rng.random() < 0.07 or ev.get(i + 1) in ("sick", "stomach") or i == tweak:
                hours = rng.uniform(4.4, 5.8)
            if sick: hours = rng.gauss(5.7, 0.5)
            if what == "travel": hours = rng.gauss(6.3, 0.4)
            bed = datetime(day.year, day.month, day.day, 22, 15) + timedelta(
                minutes=rng.randrange(0, 75) + (50 if dow in (4, 5) else 0))
            segments.extend(_night(rng, bed, hours))
            sleep_prev = hours
        heavy_prev = heavy

    _write_notes(rng, notes, days, iso, ev, tweak)
    _write(out, start, end, days, weights, daily, workouts, segments, sets, sess_min,
           nutrition, nutrients, notes)
    return out


def _night(rng, bed, hours):
    """Stage segments for one night, in ~90-minute cycles: deep early, REM late, a few
    short wakes between cycles. sleep_rows turns them into a sleep.csv row."""
    segs, t, left, c = [], bed + timedelta(minutes=rng.randrange(6, 20)), hours * 60, 0
    while left > 1:
        cyc = min(left, rng.uniform(80, 100))
        deep = cyc * (0.28 if c < 2 else 0.06) * rng.uniform(0.7, 1.2)
        rem = cyc * (0.12 if c < 2 else 0.3) * rng.uniform(0.8, 1.2)
        core_ = max(1, cyc - deep - rem)
        for stage, m in (("core", core_ * 0.6), ("deep", deep), ("core", core_ * 0.4), ("rem", rem)):
            if m >= 1:
                m = round(m)
                segs.append((t, t + timedelta(minutes=m), stage)); t += timedelta(minutes=m)
        left -= cyc; c += 1
        if left > 1 and rng.random() < 0.45:
            m = rng.randrange(2, 9)
            segs.append((t, t + timedelta(minutes=m), "awake")); t += timedelta(minutes=m)
    return segs


FEEL_GOOD = ["felt strong today", "good session, felt good", "felt great, slept well",
             "strong session, everything moved fast", "feeling good, appetite under control"]
FEEL_LOW = ["low energy all day, the cut is catching up", "rough night, woke at 3 and lay awake",
            "legs heavy and low energy, cut the last set", "wiped after work, trained anyway"]


def _runs(ev, what):
    """[(first, last)] day indexes of each unbroken run of one event."""
    out = []
    for i in sorted(k for k, w in ev.items() if w == what):
        if out and out[-1][1] == i - 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _write_notes(rng, notes, days, iso, ev, tweak):
    """health-context.txt lines: events, the back, and a scatter of how-I-feel notes."""
    for a, b in _runs(ev, "sick"):
        notes.append((iso(a), f"{iso(a)}..{iso(b)} | sick, head cold and a fever, no training"))
        notes.append((iso(a), f"{iso(a)} | sore throat, slept badly"))
        notes += [(iso(i), f"{iso(i)} | low energy, stayed home") for i in range(a + 1, b + 1)]
    for a, b in _runs(ev, "stomach"):
        notes.append((iso(a), f"{iso(a)}..{iso(b)} | stomach bug, sick for two days, barely ate"))
        notes += [(iso(i), f"{iso(i)} | wiped out, nothing stays down") for i in range(a, b + 1)]
    for a, b in _runs(ev, "travel"):
        notes.append((iso(a), f"{iso(a)}..{iso(b)} | work travel, hotel gym was a treadmill only"))
    for a, _ in _runs(ev, "deload"):
        notes.append((iso(a), f"{iso(a)} | deload week, everything light on purpose"))
    if tweak is not None:
        notes += [(iso(tweak), f"{iso(tweak)} | tweaked my lower back on the last deadlift set, stopped there"),
                  (iso(tweak + 1), f"{iso(tweak + 1)} | lower back sore this morning, resting it"),
                  (iso(tweak + 2), f"{iso(tweak + 2)} | back is tired but moving better, walked instead"),
                  (iso(tweak + 4), f"{iso(tweak + 4)} | back fine again, kept the squats light")]
    busy = {i for i in ev} | ({tweak + k for k in range(5)} if tweak is not None else set())
    mid = next(i for i in range(int(days * 0.62), days) if i not in busy)
    notes.append((iso(mid), f"{iso(mid)} | poor sleep but felt strong on bench"))
    rec = int(days * 0.2)
    notes.append((iso(rec), f"{iso(rec)} | needed extra time between sets on squats"))
    for i in range(3, days - 2):
        if i in busy or i == mid or i == rec or rng.random() > 0.14:
            continue
        text = rng.choice(FEEL_LOW if rng.random() < 0.3 else FEEL_GOOD)
        notes.append((iso(i), f"{iso(i)} | {text}"))
    notes.append((iso(days - 1), f"{iso(days - 1)} | slept well, felt good this morning"))


def _csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def _write(out, start, end, days, weights, daily, workouts, segments, sets, sess_min,
           nutrition, nutrients, notes):
    tidy = out / "tidy"
    tidy.mkdir(parents=True, exist_ok=True)
    # Ingest upserts notes and check-ins, so a database from an earlier run (another --end)
    # would keep its old ones. Everything in it comes from these files, so it goes.
    (out / "app.sqlite").unlink(missing_ok=True)
    (out / MARKER).write_text("written by python -m estebot.demo; safe to delete\n")

    _csv(tidy / "body_mass.csv", ["date", "weight_lb", "source"], [(d, w, "Scale") for d, w in weights])
    _csv(tidy / "daily.csv", T.DAILY_COLS, daily)
    _csv(tidy / "workouts.csv", T.WORKOUT_COLS, sorted(workouts, key=lambda r: (r[0], r[1])))
    nights = T.sleep_rows(segments)
    _csv(tidy / "sleep.csv", T.SLEEP_COLS, [nights[k] for k in sorted(nights)])
    _csv(tidy / "nutrition.csv", ["date", "kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g",
                                  "sodium_mg", "water_g", "meals_logged", "complete"], nutrition)
    _csv(tidy / "nutrients.csv", ["date", "nutrient", "amount", "kcal_coverage_pct"], nutrients)
    _csv(tidy / "jefit_body.csv", ["date", "weight_lb", "bodyfat_pct", "waist_in"], [])
    _write_lifting(tidy, weights, sets, sess_min)

    text = "# Notes from the phone, one line per thought. YYYY-MM-DD | text\n"
    text += "".join(line + "\n" for _, line in sorted(notes, key=lambda x: x[0]))
    (out / "health-context.txt").write_text(text)
    (out / "supplements.toml").write_text(SUPPLEMENTS.format(mag_since=(start + timedelta(days=days // 4)).isoformat()))
    _write_ledger(out, start, end, days)
    (out / "estebot.toml").write_text(CONFIG.format(data_dir=str(out).replace("'", "")))


def _write_lifting(tidy, weights, sets, sess_min):
    """sets.csv and the tables lift_tables.py builds from it, as jefit.py writes them. Pull-up
    e1RM uses the 14-day weight trend, as jefit.py does (first reading per day)."""
    bw = [(date.fromisoformat(d), w) for d, w in weights]
    idx, rows = defaultdict(int), []
    for d, name, w, reps, rpe, st in sets:
        idx[(d, name)] += 1
        load = w
        if EXERCISES[name][1] == "bw":
            t = core.trailing_mean(bw, date.fromisoformat(d))
            load = w + t if t is not None else 0.0
        e1 = core.epley_e1rm(load, reps) or 0.0
        rows.append({"date": d, "exercise": name, "muscle_group": EXERCISES[name][0],
                     "set_idx": idx[(d, name)], "weight_lb": round(w, 1), "reps": int(reps),
                     "volume_lb": round(w * reps, 1), "e1rm_lb": round(e1, 1),
                     "rpe": "" if rpe is None else f"{rpe:g}", "set_type": st, "source": "hevy"})
    rows.sort(key=lambda r: (r["date"], r["exercise"], r["set_idx"]))
    cols = ["date", "exercise", "muscle_group", "set_idx", "weight_lb", "reps", "volume_lb",
            "e1rm_lb", "rpe", "set_type", "source"]
    _csv(tidy / "sets.csv", cols, [[r[c] for c in cols] for r in rows])

    _csv(tidy / "lift_sessions.csv", L.SESSION_COLS, L.session_rows(rows, sess_min))
    _csv(tidy / "weekly_volume.csv", L.weekly_volume_cols(GROUP_COLS), L.weekly_volume_rows(rows, GROUP_COLS))
    _csv(tidy / "exercise_progression.csv", L.PROGRESSION_COLS, L.progression_rows(rows))


def _write_ledger(out, start, end, days):
    """Three open predictions and one resolved, dated relative to the span so they stay
    open when --end moves."""
    a = lambda k: (start + timedelta(days=k)).isoformat()
    resolves = (end + timedelta(days=60)).isoformat()
    base = (a(0), a(27))
    mid = a(days // 2)
    (out / "ledger.md").write_text(f"""# Prediction ledger

Each prediction is written down before the data that settles it exists.

### P-001 - The cut keeps a pace of 1 to 2 lb a week until it ends
- **Registered:** {mid}
- **Resolves:** {resolves}
- **Status:** open
- **Criterion:** least-squares slope of every weigh-in from registration to resolution,
  between -2.0 and -1.0 lb/week, with weigh-ins on at least 75% of days.

### P-002 - Squat, bench, deadlift and press stay within 5% of their pre-cut best
- **Registered:** {a(14)}
- **Resolves:** {resolves}
- **Status:** open
- **Criterion:** median change in best e1RM, last four weeks of the cut against the first four.

### P-003 - HRV ends the cut more than one SD above where it started
- **Registered:** {a(28)}
- **Resolves:** {resolves}
- **Status:** open
- **Criterion:** 30-day mean HRV at resolution vs the first four weeks, by more than their SD.

### P-004 - Food logged complete on at least 80% of days through the first deload
- **Registered:** {a(7)}
- **Resolves:** {a(int(days * 0.37))}
- **Status:** resolved: confirmed
""")
    (out / "predictions.toml").write_text(f"""# Parameters copied from ledger.md, so the page can show interim progress.

[P-001]
metric = "weight_slope"
window = ["{mid}", "{resolves}"]
band = [-2.0, -1.0]
coverage = {{ table = "weight", window = ["{mid}", "{resolves}"], need = {int(0.75 * (end + timedelta(days=60) - date.fromisoformat(mid)).days)}, max_gap = 7, label = "weigh-ins" }}

[P-002]
metric = "e1rm_median_change"
baseline = ["{base[0]}", "{base[1]}"]
window = ["{(end + timedelta(days=33)).isoformat()}", "{resolves}"]
interim_window = ["{a(28)}", "{end.isoformat()}"]
threshold_pct = -5.0
lifts = ["Barbell Squat", "Barbell Bench Press", "Barbell Deadlift", "Barbell Military Press"]

[P-003]
metric = "hrv_vs_baseline"
baseline = ["{base[0]}", "{base[1]}"]
window_days = 30
coverage = {{ table = "nutrition", window = ["{a(28)}", "{resolves}"], need = {int(0.8 * (days - 28 + 60))}, complete = true, label = "complete food logs" }}
""")


CONFIG = """\
# Written by python -m estebot.demo. Points the app at a made-up person's data.
data_dir = '{data_dir}'

[profile]
height_in = 70
birth_year = 1990
sex = "male"
units = "imperial"

[goals]
target_weight_lb = 175
max_loss_pct_per_week = 1.0
min_loss_pct_per_week = 0.5
protein_g_per_lb_target = 0.8
fat_g_per_lb_floor = 0.30

[analysis]
main_lifts = ["Barbell Squat", "Barbell Deadlift", "Barbell Bench Press", "Barbell Military Press"]
lift_streak_min_sets = 5
e1rm_max_reps = 10
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m estebot.demo", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, help="directory to write (created if missing)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--end", default=DEFAULT_END.isoformat(), help="last day, YYYY-MM-DD or 'today'")
    p.add_argument("--days", type=int, default=180)
    a = p.parse_args(argv)
    end = date.today() if a.end == "today" else date.fromisoformat(a.end)
    if a.days < 60:
        p.error("--days must be at least 60: the baselines need a month before anything is judged")
    out = Path(a.out).expanduser()
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        sys.exit(f"{out} has files in it and was not written by this command; refusing to touch it")
    generate(out, a.seed, end, a.days)
    print(f"demo data for {end - timedelta(days=a.days - 1)} to {end} in {out.resolve()}\n"
          f"  ESTEBOT_CONFIG={out.resolve() / 'estebot.toml'} python -m estebot.web.ingest")


if __name__ == "__main__":
    main()

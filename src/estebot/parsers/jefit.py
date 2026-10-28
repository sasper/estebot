#!/usr/bin/env python3
"""Jefit multi-table CSV dump -> tidy sets / sessions CSVs. Stdlib only."""
import csv, glob, os, re, sys
from collections import defaultdict
from datetime import date as _date, datetime

from estebot import config
from estebot.core import order_by_name_date, trailing_mean
from estebot.parsers import lift_tables
from estebot.parsers.jefit_raw import (merged_sections, load_renames, rename, load_corrections,
                                       correct, norm_name as _norm_name)

_cfg = config.load()

# Jefit exports are NOT append-only: a session present in an older export can be absent
# from a newer one (observed 2026-10-01, the 2026-09-02 session). Every export is therefore
# read, oldest first, and rows are unioned - a newer file corrects a day it contains, but
# cannot delete a day it simply omits.
SRCS = order_by_name_date([str(p) for p in _cfg.all_exports("jefit")])
SRC = SRCS[-1]
print(f"   jefit sources: {', '.join(os.path.basename(x) for x in SRCS)}")
OUT = str(_cfg.tidy)

# ---------- exercise renames ----------
# Same logged name, different exercise. Rules and their reasons live in renames.csv.
_RENAME = []
try:
    _RENAME = load_renames(str(_cfg.data_file("renames.csv")))
    if _RENAME:
        print(f"   renames.csv: {len(_RENAME)} rules")
except Exception as _e:
    print(f"   ! renames.csv not applied: {_e}")

def _rename(date, name, source="jefit"):
    return rename(_RENAME, date, name, source)


# ---------- user-authored corrections ----------
# Jefit changed its dumbbell entry UI around 2026-09-02. A few sessions were logged as
# COMBINED weight during the changeover before settling back to per-dumbbell, which is the
# convention before and after. Those sessions are listed in corrections.csv rather than
# patched inline, so every adjustment is visible, dated and reversible.
# min_weight spares correctly-logged sets inside a mixed session.
_CORR = []
try:
    _CORR = load_corrections(str(_cfg.data_file("corrections.csv")))
    if _CORR:
        print(f"   corrections.csv: {len(_CORR)} rules")
except Exception as _e:
    print(f"   ! corrections.csv not applied: {_e}")

_applied = [0]
def _correct(date, name, wt, source="jefit"):
    wt, hit = correct(_CORR, date, name, wt, source)
    _applied[0] += hit
    return wt

# ---------- bodyweight exercises ----------
# Pull-ups are logged at 0 lb (or the belt weight), so their e1RM was always 0 and they were
# invisible to every strength number. On a cut that hides the case that matters: the
# same 6 reps at 200 lb is about 9% less strength than at 220. Load = 14-day weight trend
# + logged weight. Only e1RM uses it; weight_lb and volume_lb stay as logged.
# The weigh-ins are chosen as the app's weight table chooses them (first reading per day).
BODYWEIGHT = {"Pull-Up", "Pull Ups", "Chin Up"}
_BW = []
_bp = _cfg.tidy_file("body_mass.csv")
if os.path.exists(_bp):
    _seen = set()
    for _r in csv.DictReader(open(_bp)):
        if _r["date"] in _seen or not _r["weight_lb"]: continue
        _seen.add(_r["date"])
        _BW.append((_date.fromisoformat(_r["date"]), float(_r["weight_lb"])))
    _BW.sort()

def _load(date_s, name, wt):
    if name not in BODYWEIGHT: return wt
    bw = trailing_mean(_BW, _date.fromisoformat(date_s))
    return wt + bw if bw is not None else 0.0   # no weigh-in in 14 days: no estimate

S = merged_sections(SRCS)

# ---------- muscle group mapping ----------
GROUPS = [
    ("olympic",   r"clean|jerk|snatch|power clean|high pull"),
    ("carry",     r"farmer|carry|suitcase|yoke"),
    ("quads",     r"squat|leg press|leg extension|lunge|hack|split squat|step.?up|sissy|bulgarian|split jump"),
    ("hamstrings",r"deadlift|leg curl|romanian|rdl|good morning|glute ham|hip thrust|glute"),
    ("calves",    r"calf|calve"),
    ("chest",     r"bench press|chest|pec |pectoral|fly|flye|dips? \(chest|push.?up|cross.?over"),
    ("back",      r"row|pulldown|pull.?up|chin.?up|lat |latissimus|shrug|pullover|rack pull|face pull"),
    ("shoulders", r"shoulder|military|overhead press|lateral raise|front raise|delt|arnold|upright row|rear raise"),
    ("biceps",    r"curl(?!.*leg)|bicep|preacher|hammer"),
    ("triceps",   r"tricep|pushdown|skull|extension \(|close grip|kickback|dip"),
    ("core",      r"crunch|ab |abs|plank|leg raise|sit.?up|russian|oblique|wood.?chop|hanging|air bike|knee raise|rollout"),
    ("cardio",    r"run|bike|cycl|elliptical|treadmill|row machine|jump rope|stair|walk"),
]
def group(name):
    n = name.lower()
    for g, pat in GROUPS:
        if re.search(pat, n): return g
    return "other"

# ---------- sets from EXERCISE LOGS ----------
SET_RE = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$")
logs = S["EXERCISE LOGS"]

# drop partial duplicates: same (date, exercise) where one log string is a prefix of another
by_key = defaultdict(list)
for r in logs:
    by_key[(r["mydate"], r["ename"])].append(r)
keep = []
for k, rs in by_key.items():
    if len(rs) == 1:
        keep.extend(rs); continue
    rs = sorted(rs, key=lambda r: len(r["logs"] or ""), reverse=True)
    kept = []
    for r in rs:
        s = (r["logs"] or "")
        if any(s and s in (o["logs"] or "") for o in kept):
            continue
        kept.append(r)
    keep.extend(kept)

def _set_row(date, name, set_idx, wt, reps, rpe=None, set_type="", source="jefit"):
    # Epley, restricted to sets of <=10 reps: above that the formula drifts badly
    # and warm-up / burnout sets add noise. 0.0 means "not a valid e1RM estimate".
    # Every set from every source goes through here, so volume and e1RM have one definition.
    load = _load(date, name, wt)
    e1rm = load * (1 + reps / 30.0) if (load > 0 and reps <= 10) else 0.0
    return {
        "date": date, "exercise": name, "muscle_group": group(name),
        "set_idx": set_idx, "weight_lb": round(wt, 1), "reps": int(reps),
        "volume_lb": round(wt * reps, 1), "e1rm_lb": round(e1rm, 1),
        "rpe": "" if rpe is None else rpe, "set_type": set_type, "source": source,
    }

sets = []
for r in keep:
    date, name = r["mydate"], r["ename"]
    if not date or date == "0000-00-00": continue
    for i, tok in enumerate((r["logs"] or "").split(",")):
        m = SET_RE.match(tok.strip())
        if not m: continue
        wt, reps = float(m.group(1)), float(m.group(2))
        if reps <= 0 or reps > 100: continue
        name = _rename(date, _norm_name(name))
        wt = _correct(date, name, wt)
        sets.append(_set_row(date, name, i + 1, wt, reps))

# ---------- Hevy: replaces Jefit on any day it covers ----------
# Hevy titles are mapped once, through hevy_names.csv. Renames and corrections apply only
# where a rule says source = hevy (an exercise that changed under the same title, a wrong
# bar weight); Jefit's own rules fix Jefit's naming and unit slips and never touch Hevy.
# A missed rep logged as 0 reps is dropped by the same rule as Jefit's, so it never
# becomes a fake single.
from estebot.parsers import hevy as _hevy
_hw = _hevy.load_snapshot(_cfg)
hevy_minutes = {}
if _hw:
    _rows, _unmapped, hevy_minutes = _hevy.hevy_sets(
        _hw, _hevy.load_names(_cfg.data_file("hevy_names.csv")))
    _hdays = {x["date"] for x in _rows}
    _replaced = sum(1 for s in sets if s["date"] in _hdays)
    sets = [s for s in sets if s["date"] not in _hdays]
    for x in _rows:
        if x["reps"] <= 0 or x["reps"] > 100: continue
        name = _rename(x["date"], x["exercise"], "hevy")
        sets.append(_set_row(x["date"], name, x["set_idx"],
                             _correct(x["date"], name, x["weight_lb"], "hevy"), x["reps"],
                             x["rpe"], x["set_type"], "hevy"))
    print(f"   hevy: {sum(not _hevy.is_imported(w) for w in _hw)} workouts on {len(_hdays)} days, replacing {_replaced} Jefit sets"
          + (f"; {len(_unmapped)} unmapped title(s), see `python -m estebot.parsers.hevy`"
             if _unmapped else ""))

sets.sort(key=lambda r: (r["date"], r["exercise"], r["set_idx"]))

with open(f"{OUT}/sets.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date","exercise","muscle_group","set_idx","weight_lb","reps",
                                      "volume_lb","e1rm_lb","rpe","set_type","source"])
    w.writeheader(); w.writerows(sets)
print(f"sets.csv: {len(sets)} sets, {len({s['date'] for s in sets})} training days, "
      f"{min(s['date'] for s in sets)} -> {max(s['date'] for s in sets)}")

# ---------- per-day lift sessions, weekly volume, per-exercise progression ----------
# The tables themselves are built in lift_tables.py, which demo.py uses too.
days = sorted({s["date"] for s in sets})
sess_meta = {}
for r in S.get("WORKOUT SESSIONS", []):
    try:
        d = datetime.fromtimestamp(int(r["starttime"])).date().isoformat()
    except Exception:
        continue
    m = sess_meta.setdefault(d, {"total_time": 0.0, "workout_time": 0.0, "calories": 0.0})
    for k, src in (("total_time","total_time"), ("workout_time","workout_time"), ("calories","calories")):
        try: m[k] += float(r.get(src) or 0)
        except ValueError: pass

minutes, kcal = {}, {}
for d in days:
    m = sess_meta.get(d, {})
    if d in hevy_minutes:
        # Hevy's start/end replaces Jefit's timer. Hevy has no calorie estimate.
        minutes[d] = round(hevy_minutes[d], 1)
    else:
        if m.get("total_time"): minutes[d] = round(m["total_time"] / 60, 1)
        if m.get("calories"): kcal[d] = round(m["calories"])

def _write(name, header, rows):
    with open(f"{OUT}/{name}", "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return rows

_write("lift_sessions.csv", lift_tables.SESSION_COLS, lift_tables.session_rows(sets, minutes, kcal))
print(f"lift_sessions.csv: {len(days)} sessions")

groups = [g for g, _ in GROUPS] + ["other"]
wk = _write("weekly_volume.csv", lift_tables.weekly_volume_cols(groups),
            lift_tables.weekly_volume_rows(sets, groups))
print(f"weekly_volume.csv: {len(wk)} weeks")

prog = _write("exercise_progression.csv", lift_tables.PROGRESSION_COLS, lift_tables.progression_rows(sets))
print(f"exercise_progression.csv: {len({r[0] for r in prog})} exercises")

# ---------- jefit body stats ----------
with open(f"{OUT}/jefit_body.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date","weight_lb","bodyfat_pct","waist_in"])
    seen = set()
    for r in sorted(S.get("PROFILE", []), key=lambda r: r.get("mydate") or ""):
        d = r.get("mydate")
        if not d or d in seen or d == "0000-00-00": continue
        seen.add(d)
        w.writerow([d, r.get("weight"), r.get("fatpercent"), r.get("waist")])
print("jefit_body.csv written")

#!/usr/bin/env python3
import csv, os, statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta

from estebot import config

_cfg = config.load()
T = str(_cfg.tidy)
def load(n): return list(csv.DictReader(open(f"{T}/{n}")))
def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None
def d(s): return datetime.strptime(s, "%Y-%m-%d").date()

daily, sleep, wo, sets, sess = load("daily.csv"), load("sleep.csv"), load("workouts.csv"), load("sets.csv"), load("lift_sessions.csv")
body = load("body_mass.csv")

# Comparison windows come from the data: the last four weeks, the rest of that calendar
# year before them, and the whole previous year.
TODAY = max(d(r["date"]) for r in daily + sets) if (daily or sets) else date.today()
RECENT = TODAY - timedelta(days=27)
YEAR, PRIOR = date(TODAY.year, 1, 1), TODAY.year - 1
def wk(a, b): return max((b - a).days, 1) / 7

# ---------- combined daily ----------
D = {r["date"]: dict(r) for r in daily}
for r in sleep: D.setdefault(r["night"], {"date": r["night"]}).update(
    {"sleep_hr": r["asleep_hr"], "deep_hr": r["deep_hr"], "rem_hr": r["rem_hr"], "sleep_eff": r["efficiency_pct"]})
for r in sess: D.setdefault(r["date"], {"date": r["date"]}).update(
    {"lift_sets": r["n_sets"], "lift_volume": r["total_volume_lb"], "lift_groups": r["muscle_groups"]})
cols = ["date","steps","active_kcal","exercise_min","resting_hr","hrv_sdnn","hr_max","sleep_hr","deep_hr","rem_hr","sleep_eff","lift_sets","lift_volume","lift_groups"]
with open(f"{T}/daily_combined.csv","w",newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
    for k in sorted(D): w.writerow({**{c:"" for c in cols}, **D[k]})

def window(rows, key, a, b):
    return [r for r in rows if a <= d(r[key]) <= b]
def avg(rows, col):
    v = [f(r.get(col)) for r in rows]; v = [x for x in v if x is not None]
    return round(st.mean(v), 1) if v else None

A=str(_cfg.data_file("annotations.csv"))
if os.path.exists(A):
    print("="*78); print("CONTEXT ANNOTATIONS — read these before interpreting any dip or spike"); print("="*78)
    for r in csv.DictReader(open(A)):
        span = r["start_date"] + (f" -> {r['end_date']}" if r["end_date"] else "")
        print(f"  {span:<26}[{r['type']}] {r['label']}")
        print(f"  {'':<26}{r['note']}")
    print()
def span(rows): return f"{min(r['date'] for r in rows)} -> {max(r['date'] for r in rows)}" if rows else "none"
print("="*78); print(f"SUMMARY  (Apple Health {span(daily)} | lifting log {span(sets)})"); print("="*78)

periods = [("last 4 weeks", RECENT, TODAY),
           (f"{YEAR.year} before that", YEAR, RECENT - timedelta(days=1)),
           (f"{PRIOR} full year", date(PRIOR,1,1), date(PRIOR,12,31))]
print(f"\n{'':34}{'last 4 wk':>13}{f'{YEAR.year} pre':>11}{PRIOR:>10}")
rows_out = []
def line(label, vals): print(f"{label:<34}" + "".join(f"{('' if v is None else v):>13}" if i==0 else f"{('' if v is None else v):>11}" if i==1 else f"{('' if v is None else v):>10}" for i,v in enumerate(vals)))

metrics = [("training days / week", lambda a,b: round(len({r['date'] for r in window(sess,'date',a,b)})/wk(a,b),1)),
           ("lifting sets / week",  lambda a,b: round(len(window(sets,'date',a,b))/wk(a,b))),
           ("volume klb / week",    lambda a,b: round(sum(f(r['volume_lb']) or 0 for r in window(sets,'date',a,b))/wk(a,b)/1000)),
           ("cardio+other workouts / wk", lambda a,b: round(len([r for r in window(wo,'date',a,b) if r['activity']!='TraditionalStrengthTraining'])/wk(a,b),1)),
           ("steps / day",          lambda a,b: avg(window(daily,'date',a,b),'steps')),
           ("active kcal / day",    lambda a,b: avg(window(daily,'date',a,b),'active_kcal')),
           ("exercise min / day",   lambda a,b: avg(window(daily,'date',a,b),'exercise_min')),
           ("resting HR",           lambda a,b: avg(window(daily,'date',a,b),'resting_hr')),
           ("HRV (SDNN ms)",        lambda a,b: avg(window(daily,'date',a,b),'hrv_sdnn')),
           ("sleep hours",          lambda a,b: avg(window(sleep,'night',a,b),'asleep_hr')),
           ("deep sleep hours",     lambda a,b: avg(window(sleep,'night',a,b),'deep_hr')),
           ("sleep efficiency %",   lambda a,b: avg(window(sleep,'night',a,b),'efficiency_pct'))]
for name, fn in metrics:
    line(name, [fn(a,b) for _,a,b in periods])

# ---------- weight ----------
print("\n" + "-"*78); print("BODY WEIGHT — every record in either source")
allw = sorted([(r["date"], f(r["weight_lb"]), "AppleHealth") for r in body if f(r["weight_lb"])]
            + [(r["date"], f(r["weight_lb"]), "Jefit") for r in load("jefit_body.csv") if f(r["weight_lb"])])
for dt, v, s in allw[-12:]: print(f"  {dt}  {v:>6.1f} lb   ({s})")
print(f"  total records ever: {len(allw)}   in {YEAR.year}: {len([x for x in allw if x[0]>=YEAR.isoformat()])}")

# ---------- strength progression ----------
print("\n" + "-"*78); print("STRENGTH — lifts trained in the last 4 weeks, best e1RM by period")
prog = load("exercise_progression.csv")
recent_ex = {r["exercise"] for r in prog if d(r["date"]) >= RECENT}
def best(ex, a, b):
    v = [f(r["best_e1rm_lb"]) for r in prog if r["exercise"]==ex and a <= d(r["date"]) <= b]
    return max(v) if v else None
print(f"  {'exercise':<34}{PRIOR:>8}{f'{YEAR.year} pre':>10}{'last4wk':>9}{'chg':>8}")
out=[]
for ex in recent_ex:
    y25, pre, post = best(ex, date(PRIOR,1,1), date(PRIOR,12,31)), best(ex, YEAR, RECENT - timedelta(days=1)), best(ex, RECENT, TODAY)
    base = pre or y25
    out.append((ex, y25, pre, post, (round(100*(post-base)/base,1) if base and post else None)))
for ex,y25,pre,post,chg in sorted(out, key=lambda r: -(r[3] or 0)):
    fm=lambda v: f"{v:.0f}" if v else "-"
    print(f"  {ex[:33]:<34}{fm(y25):>8}{fm(pre):>10}{fm(post):>9}{(f'{chg:+.1f}%' if chg is not None else '-'):>8}")

# ---------- weekly volume by group, last 10 wks ----------
print("\n" + "-"*78); print("WEEKLY SETS BY MUSCLE GROUP — last 10 weeks")
wv = load("weekly_volume.csv")
groups = ["olympic","quads","hamstrings","calves","chest","back","shoulders","biceps","triceps","core","carry"]
print("  week      " + "".join(f"{g[:5]:>7}" for g in groups) + f"{'TOT':>7}")
for r in wv[-10:]:
    print(f"  {r['week']:<10}" + "".join(f"{r.get(g+'_sets','0'):>7}" for g in groups) + f"{r['total_sets']:>7}")

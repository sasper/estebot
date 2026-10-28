#!/usr/bin/env python3
"""Non-overlapping 10-week windows across the whole Jefit history; score strength outcome per window
and correlate against training variables."""
import csv, os, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta

from estebot import config

_cfg = config.load()
T = str(_cfg.tidy)
d = lambda s: datetime.strptime(s, "%Y-%m-%d").date()
sets = list(csv.DictReader(open(f"{T}/sets.csv")))
for s in sets:
    s["date_o"]=d(s["date"]); s["weight_lb"]=float(s["weight_lb"]); s["reps"]=int(s["reps"])
    s["volume_lb"]=float(s["volume_lb"]); s["e1rm_lb"]=float(s["e1rm_lb"])
KEY=["Barbell Squat","Barbell Deadlift","Barbell Bench Press","Barbell Military Press",
     "Barbell Bent-Over Row","Barbell Front Squat","Barbell Clean","Barbell Stiff-Leg Deadlift",
     "Dumbbell Bench Press","Barbell Incline Bench Press","Cable Seated Row","Machine Leg Extension"]
by_day=defaultdict(list)
for s in sets: by_day[s["date_o"]].append(s)
days=sorted(by_day)
WIN, EDGE = 70, 28    # 10-week window, 4-week edges for early/late comparison

def e1rm(lift, ds):
    v=[s["e1rm_lb"] for dd in ds for s in by_day[dd] if s["exercise"]==lift and s["reps"]<=10]
    return max(v) if v else None

start=days[0]; wins=[]
while start <= days[-1]:
    end=start+timedelta(days=WIN-1)
    ds=[x for x in days if start<=x<=end]
    if len(ds)>=8:
        early=[x for x in ds if x<start+timedelta(days=EDGE)]
        late =[x for x in ds if x>end-timedelta(days=EDGE)]
        chg=[]
        for lift in KEY:
            a,z=e1rm(lift,early),e1rm(lift,late)
            if a and z and a>0: chg.append(100*(z-a)/a)
        if len(chg)>=3 and early and late:
            ws=[s for dd in ds for s in by_day[dd]]
            gs=defaultdict(set)
            for dd in ds:
                for g in {s["muscle_group"] for s in by_day[dd]}: gs[g].add(dd)
            main=[g for g in ("chest","back","quads","hamstrings","shoulders") if gs.get(g)]
            wins.append(dict(
                label=f"{start} ({len(ds)} ses)", start=start,
                spw=len(ds)/(WIN/7), setspw=len(ws)/(WIN/7),
                volpw=sum(s["volume_lb"] for s in ws)/(WIN/7)/1000,
                setsper=len(ws)/len(ds),
                grpper=st.mean(len({s['muscle_group'] for s in by_day[dd]}) for dd in ds),
                freq=st.mean(len(gs[g]) for g in main)/(WIN/7) if main else 0,
                nex=len({s["exercise"] for s in ws}),
                cmpd=100*sum(1 for s in ws if s["exercise"] in KEY)/len(ws),
                avgreps=st.mean(s["reps"] for s in ws),
                strength=st.median(chg), nlifts=len(chg)))
    start=end+timedelta(days=1)

print(f"{len(wins)} usable non-overlapping 10-week windows, 2017-2026\n")
h=f"{'window start':<14}{'ses/wk':>7}{'sets/wk':>8}{'klb/wk':>7}{'sets/ses':>9}{'grp/ses':>8}{'freq/grp':>9}{'#ex':>5}{'cmpd%':>7}{'avgrep':>7}{'STRENGTH':>10}"
print(h); print("-"*len(h))
for w in wins:
    print(f"{str(w['start']):<14}{w['spw']:>7.1f}{w['setspw']:>8.0f}{w['volpw']:>7.0f}{w['setsper']:>9.0f}"
          f"{w['grpper']:>8.1f}{w['freq']:>9.2f}{w['nex']:>5}{w['cmpd']:>7.0f}{w['avgreps']:>7.1f}{w['strength']:>+9.1f}%")

print("\n"+"="*80); print("WHAT CORRELATES WITH GAINING STRENGTH  (Pearson r vs 10-wk e1RM change)"); print("="*80)
feats=[("sessions / week","spw"),("sets / week","setspw"),("volume klb / week","volpw"),
       ("sets per session","setsper"),("muscle groups per session","grpper"),
       ("sessions per muscle group / wk","freq"),("distinct exercises","nex"),
       ("% sets on key compounds","cmpd"),("average reps per set","avgreps")]
y=[w["strength"] for w in wins]
res=[]
for name,c in feats:
    x=[w[c] for w in wins]
    try: res.append((name, st.correlation(x,y)))
    except Exception: pass
for name,r in sorted(res,key=lambda t:-abs(t[1])):
    bar="#"*int(abs(r)*40)
    print(f"  {name:<32}{r:>+6.2f}  {bar}")

print("\nTOP 5 vs BOTTOM 5 WINDOWS")
s_=sorted(wins,key=lambda w:-w["strength"])
for lbl,grp in (("BEST ",s_[:5]),("WORST",s_[-5:])):
    print(f"  {lbl}: strength {st.mean(w['strength'] for w in grp):+.1f}%  |  "
          f"{st.mean(w['spw'] for w in grp):.1f} ses/wk  {st.mean(w['setspw'] for w in grp):.0f} sets/wk  "
          f"{st.mean(w['setsper'] for w in grp):.0f} sets/ses  {st.mean(w['grpper'] for w in grp):.1f} grp/ses  "
          f"{st.mean(w['freq'] for w in grp):.2f} freq/grp  {st.mean(w['nex'] for w in grp):.0f} exercises  "
          f"{st.mean(w['avgreps'] for w in grp):.1f} reps")

#!/usr/bin/env python3
"""Cronometer 'Daily Nutrition' export -> tidy nutrition.csv (one row per day, Total rows only).

Merges EVERY file matching [sources.cronometer] export_glob (oldest file first, newest file
wins per date), so a narrow re-export - e.g. re-pulling one week after ticking Completed -
updates those days without destroying history. Pass explicit paths as args to override the glob.
"""
import csv, glob, os, sys

from estebot import config

_cfg = config.load()

ARGS = sys.argv[1:]
# Manual exports plus the automatic pulls (cronometer_pull.py), oldest file first.
_PULLED = (_cfg.data_file("raw") / "cronometer").glob("dailysummary-*.csv")
SRCS = ARGS or [str(p) for p in sorted([*_cfg.all_exports("cronometer"), *_PULLED],
                                       key=os.path.getmtime)]
OUT = str(_cfg.tidy_file("nutrition.csv"))
WANT = [("Energy (kcal)","kcal"),("Protein (g)","protein_g"),("Carbs (g)","carbs_g"),
        ("Fat (g)","fat_g"),("Fiber (g)","fiber_g"),("Sugars (g)","sugar_g"),
        ("Sodium (mg)","sodium_mg"),("Water (g)","water_g")]
f = lambda v: (float(v) if v not in (None, "") else None)

days = {}   # date -> (row fields, source file)
for src in SRCS:
    rows = list(csv.DictReader(open(src)))
    tot = [r for r in rows if r.get("Group","").strip('"') == "Total"]
    kept = 0
    for r in tot:
        if f(r.get("Energy (kcal)")) is None: continue
        day = r["Date"]
        meals = [m["Group"].strip('"') for m in rows
                 if m["Date"] == day and m.get("Group","").strip('"') != "Total" and f(m.get("Energy (kcal)"))]
        groups = [m for m in rows if m["Date"] == day and m.get("Group","").strip('"') != "Total"]
        days[day] = (r, meals, os.path.basename(src), groups)   # later file wins
        kept += 1
    print(f"   {os.path.basename(src)}: {kept} days")

with open(OUT, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["date"]+[o for _,o in WANT]+["meals_logged","complete"])
    for day in sorted(days):
        r, meals, _, _ = days[day]
        w.writerow([day]+[(round(f(r.get(c)),1) if f(r.get(c)) is not None else "") for c,_ in WANT]
                   +[len(meals), r.get("Completed","").strip().lower()])

comp = sum(1 for d in days if days[d][0].get("Completed","").strip().lower() == "true")
print(f"nutrition.csv: {len(days)} days merged from {len(SRCS)} file(s), {comp} marked complete")

# ---------- every nutrient, with how much of the day reported it ----------
# A day's Total row is never blank: a meal with no data for a nutrient just adds nothing,
# so the total reads low and looks like a deficiency. The per-meal rows do show the blanks,
# so each nutrient carries the share of the day's kcal that came from meals reporting it.
# Gaps are only judged on days where that share is high (analysis/nutrition.py).
SKIP = {"Date", "Group", "Completed", "Energy (kcal)"}
NOUT = str(_cfg.tidy_file("nutrients.csv"))
with open(NOUT, "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["date", "nutrient", "amount", "kcal_coverage_pct"])
    for day in sorted(days):
        r, _, _, groups = days[day]
        kcal = sum(f(g.get("Energy (kcal)")) or 0 for g in groups)
        for col in r:
            if col in SKIP or col is None or f(r.get(col)) is None: continue
            covered = sum(f(g.get("Energy (kcal)")) or 0 for g in groups if f(g.get(col)) is not None)
            w.writerow([day, col, round(f(r[col]), 3), round(100 * covered / kcal, 1) if kcal else ""])
print(f"nutrients.csv: {len(days)} days, every Cronometer nutrient with its kcal coverage")

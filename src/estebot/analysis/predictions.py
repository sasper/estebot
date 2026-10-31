"""The prediction ledger on the page: what is open, when it resolves, and where it stands.

ledger.md (data repo) is the record: claims, criteria, amendments, resolutions, in prose.
predictions.toml (data repo) carries only the parameters a criterion needs to be computed,
copied from the ledger as registered. The code knows metric TYPES, never a prediction's
numbers, so no personal threshold lives in this repo.

Progress shown here is interim. A prediction resolves in the ledger, by the criterion as
written there, including the judgement calls (confounds, amendments) a number cannot make.
"""
import re
import statistics as st
import tomllib
from datetime import date, timedelta

from estebot import core

_HEAD = re.compile(r"^###\s+(P-\d+)\s+[—-]\s+(.+?)\s*$")
_FIELD = re.compile(r"^-\s+\*\*(Status|Resolves|Registered):\*\*\s*(.+?)\s*$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_ledger(text):
    """-> [{id, title, status, resolves, registered}] in ledger order. `resolves` is the
    last date on the Resolves line ("when ... or 2027-03-31" -> 2027-03-31)."""
    out, cur = [], None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            if m.group(1) == "P-NNN":
                cur = None; continue
            cur = {"id": m.group(1), "title": m.group(2), "status": "",
                   "resolves": None, "registered": None}
            out.append(cur); continue
        f = _FIELD.match(line) if cur else None
        if f:
            key, val = f.group(1).lower(), f.group(2)
            if key == "status":
                cur["status"] = val.replace("*", "").strip()
            else:
                dates = _DATE.findall(val)
                cur[key] = dates[-1] if dates else None
    return out


def load_specs(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------- metric types
def _clip(window, day):
    a, b = window
    return a, min(b, day)

def _coverage(con, engine, spec, day):
    """Logged days so far against the need, with excused days out of the denominator."""
    c = spec["coverage"]
    a, b = c["window"]
    end = min(b, day)
    logged, denom, exc, _ = engine.coverage(con, c["table"], a, end,
                                            require_complete=c.get("complete", False),
                                            column=c.get("column"))
    total = (date.fromisoformat(b) - date.fromisoformat(a)).days + 1
    elapsed = (date.fromisoformat(end) - date.fromisoformat(a)).days + 1
    gap = engine.longest_gap(con, c["table"], a, end) if "max_gap" in c else None
    # excused days leave the denominator (ledger rule), so the requirement shrinks with them
    on_track = logged >= c["need"] / total * (elapsed - exc)
    if gap is not None and gap > c["max_gap"]:
        on_track = False
    return {"label": c.get("label", c["table"]), "logged": logged, "need": c["need"],
            "total": total, "excused": exc, "gap": gap, "max_gap": c.get("max_gap"),
            "on_track": on_track}

def weight_slope(con, engine, spec, day):
    a, b = _clip(spec["window"], day)
    pts = engine.weight_series(con, a, b)
    slope = core.ols_slope_per_week(pts)
    lo, hi = spec["band"]
    inside = slope is not None and lo <= slope <= hi
    return {"value": None if slope is None else f"{slope:+.2f} lb/week",
            "target": f"{lo:+.1f} to {hi:+.1f} lb/week", "inside": inside if slope is not None else None,
            "detail": f"line fitted since {date.fromisoformat(a):%-d %b %Y} ({len(pts)} weigh-ins)"}

def e1rm_median_change(con, engine, spec, day):
    final = spec["window"]
    interim = day < final[0]
    w = _clip(spec.get("interim_window", final) if interim else final, day)
    med, per = engine.e1rm_median_change(con, tuple(spec["baseline"]), w, spec["lifts"])
    thr = spec["threshold_pct"]
    return {"value": None if med is None else f"{med:+.1f} %", "per": per, "window": w,
            "target": f"not below {thr:+.1f} %", "inside": None if med is None else med >= thr,
            "detail": ("interim: " if interim else "") + f"{w[0]} to {w[1]} vs {spec['baseline'][0]} to "
                      f"{spec['baseline'][1]}; " + ", ".join(f"{engine.lift_label(k)} {v:+.1f}%"
                                                            for k, v in sorted(per.items()))}

def hrv_vs_baseline(con, engine, spec, day):
    rows = lambda a, b: [r["hrv_sdnn"] for r in con.execute(
        "SELECT hrv_sdnn FROM daily WHERE date BETWEEN ? AND ? AND hrv_sdnn IS NOT NULL", (a, b))]
    base = rows(*spec["baseline"])
    cur_start = (date.fromisoformat(day) - timedelta(days=spec.get("window_days", 30) - 1)).isoformat()
    cur = rows(cur_start, day)
    if len(base) < 2 or not cur:
        return {"value": None, "target": "", "inside": None, "detail": "not enough HRV data"}
    bm, sd, cm = st.mean(base), st.pstdev(base), st.mean(cur)
    return {"value": f"{cm:.1f} ms ({cm - bm:+.1f})", "target": f"more than {bm + sd:.1f} ms "
            f"(baseline {bm:.1f} + 1 SD {sd:.1f})", "inside": cm - bm > sd,
            "detail": f"30-day mean to {day} vs {spec['baseline'][0]} to {spec['baseline'][1]}"}

METRICS = {"weight_slope": weight_slope, "e1rm_median_change": e1rm_median_change,
           "hrv_vs_baseline": hrv_vs_baseline}


def panel(con, engine, ledger_text, specs, day):
    """Open predictions with interim progress where a spec says how to compute it."""
    out = []
    for p in parse_ledger(ledger_text):
        if not p["status"].startswith("open"):
            continue
        spec = specs.get(p["id"], {})
        row = dict(p, progress=None, coverage=None, caveat=spec.get("caveat"),
                   private=bool(spec.get("private")))
        if p["resolves"]:
            row["days_left"] = (date.fromisoformat(p["resolves"]) - date.fromisoformat(day)).days
        if spec.get("metric") in METRICS:
            row["progress"] = METRICS[spec["metric"]](con, engine, spec, day)
        if "coverage" in spec:
            row["coverage"] = _coverage(con, engine, spec, day)
        out.append(row)
    return out

"""Reading Jefit's multi-table CSV dump, shared by jefit.py (the tidy tables) and
hevy_import.py (the history import). No I/O beyond reading the files, and no output.

jefit.py is a script that writes the tidy tables when imported, so anything a second
caller needs lives here instead."""
import csv, io, os, re


def sections(path):
    secs, cur, buf = {}, None, []
    for ln in open(path, encoding="utf-8", errors="replace").read().split("\n"):
        if ln.startswith("###"):
            if len(ln) > 3 and ln[3] == " ":
                if cur: secs[cur] = buf
                cur, buf = ln.strip("# ").strip(), []
            else:
                if cur: secs[cur] = buf
                cur, buf = None, []
            continue
        if cur is not None and ln.strip():
            buf.append(ln)
    if cur: secs[cur] = buf
    return {k: list(csv.DictReader(io.StringIO("\n".join(v)))) for k, v in secs.items() if len(v) > 1}

def merged_sections(paths):
    """Union every export's sections, oldest file first. Rows are deduped on their full
    content, so a day present in any export survives, while a newer export's corrections
    to a row it does contain still land."""
    out, seen = {}, {}
    for path in paths:
        for name, rows in sections(path).items():
            bucket = out.setdefault(name, [])
            keys = seen.setdefault(name, set())
            for r in rows:
                k = tuple(sorted((str(k2), str(v)) for k2, v in r.items()))
                if k not in keys:
                    keys.add(k); bucket.append(r)
    return out


def norm_name(n):
    """Jefit stores both ASCII and curly apostrophes for the same exercise - "Farmer's Carry"
    (174 sets) and "Farmer’s Carry" (25 sets) were being tracked as two exercises,
    silently splitting a 199-set progression. Normalise typographic variants at parse time."""
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), (" ", " ")):
        n = n.replace(a, b)
    return re.sub(r"\s+", " ", n).strip()


# Same logged name, different exercise - usually an implement or venue change. Renaming at
# parse time keeps the two series from being compared as one. See renames.csv for reasons.
def load_renames(path):
    """-> [(from_date, to_date, exercise, new_name, source)]; an open to_date runs forever.
    source says whose sets a rule renames: blank = Jefit (every rule before 17 Oct 2026),
    or "hevy", where a title maps to one name for good and only a dated rule can say the
    exercise changed (17 Oct 2026: carries moved from uphill to flat)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [(r["from_date"], r["to_date"] or "9999-99-99", r["exercise"], r["new_name"],
                 (r.get("source") or "jefit").strip().lower()) for r in csv.DictReader(f)]

def rename(rules, date, name, source="jefit"):
    for a, b, ex, new, src in rules:
        if src == source and ex == name and a <= date <= b: return new
    return name

def load_corrections(path):
    """corrections.csv -> [(from_date, to_date, exercise, factor, min_weight, add_lb, source)].

    A rule covers one date unless to_date is set, so the 2026 dumbbell rules stay one-day
    fixes while a convention that ran for years (Jefit's +10 lb on pull-ups) is one row.
    weight -> weight * factor + add_lb: a factor for unit slips (combined vs per-hand), an
    offset for a wrong bar weight (the open hex bar is 55 lb, logged as 45). source says
    whose sets a rule fixes: blank = Jefit (every rule before 17 Oct 2026), or "hevy"."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [(r["date"], r.get("to_date") or r["date"], r["exercise"], float(r["factor"]),
                 float(r["min_weight"]), float(r.get("add_lb") or 0),
                 (r.get("source") or "jefit").strip().lower()) for r in csv.DictReader(f)]

def correct(rules, date, name, wt, source="jefit"):
    """-> (weight, applied). min_weight spares correctly-logged sets in a mixed session."""
    for a, b, ex, factor, floor, add, src in rules:
        if src == source and ex == name and a <= date <= b:
            if wt < floor: return wt, False
            return wt * factor + add, True
    return wt, False

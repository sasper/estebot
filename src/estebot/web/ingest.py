"""tidy/*.csv + annotation sources -> app.sqlite.

Idempotent: safe to re-run after every ./refresh.sh. Cached tables are replaced
wholesale (they are derived); annotations are upserted (they are authored).
"""
import csv, os, re, sys
from datetime import date
from estebot.web import db

from estebot import config, core

_cfg = config.load()

ROOT = str(_cfg.data_dir)
TIDY = str(_cfg.tidy)

def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def _rows(name):
    p = os.path.join(TIDY, name)
    if not os.path.exists(p):
        print(f"   ! missing {name}"); return []
    with open(p) as fh: return list(csv.DictReader(fh))

def load_tidy(con):
    specs = [
        ("weight", "body_mass.csv", "date",
         lambda r: (r["date"], _f(r["weight_lb"]), r.get("source"))),
        ("nutrition", "nutrition.csv", "date",
         lambda r: (r["date"], _f(r["kcal"]), _f(r["protein_g"]), _f(r["carbs_g"]),
                    _f(r["fat_g"]), _f(r["fiber_g"]), _f(r["sodium_mg"]),
                    int(r["meals_logged"] or 0),
                    1 if str(r.get("complete","")).lower() == "true" else 0)),
        ("daily", "daily.csv", "date",
         lambda r: (r["date"], _f(r.get("steps")), _f(r.get("active_kcal")),
                    _f(r.get("exercise_min")), _f(r.get("resting_hr")),
                    _f(r.get("hrv_sdnn")), _f(r.get("resp_rate")), _f(r.get("spo2")),
                    _f(r.get("basal_kcal")))),
        ("sleep", "sleep.csv", "night",
         lambda r: (r["night"], _f(r.get("asleep_hr")), _f(r.get("deep_hr")),
                    _f(r.get("rem_hr")), _f(r.get("efficiency_pct")), _f(r.get("n_wakes")))),
        ("lift_session", "lift_sessions.csv", "date",
         lambda r: (r["date"], int(r["n_exercises"] or 0), int(r["n_sets"] or 0),
                    _f(r["total_volume_lb"]), _f(r["top_set_lb"]),
                    r.get("muscle_groups"), _f(r.get("session_min")),
                    int(r["hard_sets"]) if r.get("hard_sets") not in (None, "") else None,
                    _f(r.get("mean_rpe")))),
        ("e1rm", "exercise_progression.csv", None,
         lambda r: (r["exercise"], r["muscle_group"], r["date"], _f(r["best_e1rm_lb"]),
                    _f(r["best_set_weight_lb"]), int(r["best_set_reps"] or 0))),
    ]
    for table, fname, _key, fn in specs:
        rows = _rows(fname)
        if not rows: continue
        tuples, seen = [], set()
        for r in rows:
            t = fn(r)
            if t[0] is None: continue
            k = (t[0], t[2]) if table == "e1rm" else t[0]
            if k in seen:            # body_mass.csv has genuine same-day duplicates
                continue
            seen.add(k); tuples.append(t)
        con.execute(f"DELETE FROM {table}")
        con.executemany(f"INSERT INTO {table} VALUES ({','.join('?'*len(tuples[0]))})", tuples)
        print(f"   {table:14s} {len(tuples):>5} rows")

# --- annotation sources -------------------------------------------------------
# Three capture surfaces exist by accident of history; all are read, none is lost.
#   annotations.csv     - structured, written by earlier analysis sessions
#   health-context.txt  - iCloud plain text, written from the phone via Shortcut
# context-log.md stays a human narrative document and is not parsed.

EXCUSE_HINTS = re.compile(
    r"travel|away|parents|hospital|surgery|camping|vacation|holiday|wedding|funeral|"
    r"sick|ill|flu|injur|emergency", re.I)

def load_workouts(con):
    """Apple workouts, several per day, so like nutrients it skips the one-row-per-date
    loader. The rest-day rule needs to know a non-lifting session happened, not just minutes."""
    rows = _rows("workouts.csv")
    con.execute("DELETE FROM workout")
    con.executemany("INSERT OR REPLACE INTO workout VALUES (?,?,?,?)",
                    [(r["date"], r["start"], r["activity"], _f(r["duration_min"])) for r in rows])
    print(f"   {'workout':14s} {len(rows):>5} rows")

def load_sets(con):
    """sets.csv: several rows per day and exercise, so it skips the one-row-per-date loader."""
    rows = _rows("sets.csv")
    con.execute("DELETE FROM lift_set")
    con.executemany("INSERT INTO lift_set VALUES (?,?,?,?,?,?,?)",
                    [(r["date"], r["exercise"], int(r["set_idx"] or 0), _f(r["weight_lb"]),
                      int(_f(r["reps"]) or 0), _f(r.get("rpe")), r.get("set_type") or None)
                     for r in rows])
    print(f"   {'lift_set':14s} {len(rows):>5} rows")

def load_nutrients(con):
    """nutrients.csv is long (one row per day and nutrient), so it skips the one-row-per-
    date loader above."""
    rows = _rows("nutrients.csv")
    con.execute("DELETE FROM nutrient")
    con.executemany("INSERT OR REPLACE INTO nutrient VALUES (?,?,?,?)",
                    [(r["date"], r["nutrient"], _f(r["amount"]), _f(r["kcal_coverage_pct"]))
                     for r in rows])
    print(f"   {'nutrient':14s} {len(rows):>5} rows")

def load_annotations(con):
    n = 0
    p = os.path.join(ROOT, "annotations.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            if not r.get("start_date"): continue
            note = r.get("note") or ""
            con.execute(
                """INSERT OR REPLACE INTO annotation
                   (start_date,end_date,type,label,note,source,excused)
                   VALUES (?,?,?,?,?,?,?)""",
                (r["start_date"], r.get("end_date") or None, r.get("type"),
                 r.get("label"), note, "annotations.csv",
                 1 if EXCUSE_HINTS.search(f"{r.get('label','')} {note}") else 0))
            n += 1

    p = os.path.join(ROOT, "health-context.txt")
    if os.path.exists(p):
        # "YYYY-MM-DD | text"  or  "YYYY-MM-DD..YYYY-MM-DD | text"
        pat = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\.\.(\d{4}-\d{2}-\d{2}))?\s*\|\s*(.+?)\s*$")
        for line in open(p):
            if line.lstrip().startswith("#"): continue
            m = pat.match(line)
            if not m: continue
            start, end, text = m.groups()
            label = text.split(",")[0][:60]
            con.execute(
                """INSERT OR REPLACE INTO annotation
                   (start_date,end_date,type,label,note,source,excused)
                   VALUES (?,?,?,?,?,?,?)""",
                (start, end, "context", label, text, "health-context.txt",
                 1 if EXCUSE_HINTS.search(text) else 0))
            n += 1
    print(f"   {'annotation':14s} {n:>5} rows")

def run():
    con = db.init()
    print("ingesting:")
    load_tidy(con)
    load_nutrients(con)
    load_sets(con)
    load_workouts(con)
    load_annotations(con)
    parse_subjective(con)
    con.commit()
    cur = con.execute("SELECT MAX(date) d FROM weight").fetchone()
    print(f"\nlatest weigh-in: {cur['d']}")
    return con



# ---------------------------------------------------------------- subjective signals
# The Shortcut writes free text to health-context.txt. The readiness engine reads structured
# fields. This bridges the two with conservative keyword rules - it only writes a field when
# the signal is unambiguous, and it NEVER overwrites a manually entered check-in.
#
# Where a line carries conflicting signals (e.g. "poor sleep ... pull-ups were very strong"),
# nothing is inferred: the row is flagged needs_review so the app asks rather than guesses.
# Five of five anomalies in this dataset needed a human to resolve. Guessing has a bad record.

BACK_SPASM = re.compile(r"spasm", re.I)
# "tender" and "injur*" are ordinary ways to describe a sore back; without them a note
# like "back is tender" produced no check-in at all.
BACK_SORE  = re.compile(r"back\b[^.]{0,30}(sore|hurt|pain|tweak|tender|injur)|"
                        r"(sore|hurt|painful|tweak\w*|tender|injur\w*)[^.]{0,30}\bback\b", re.I)
BACK_TIRED = re.compile(r"back\b[^.]{0,30}tired|tired[^.]{0,30}\bback\b", re.I)
ENERGY_LOW = re.compile(r"not feeling (?:super |very )?strong|poor sleep|bad sleep|didn'?t sleep|"
                        r"rough night|exhausted|wiped|low energy|run down|recovery[^.]{0,20}hard", re.I)
ENERGY_HIGH= re.compile(r"feeling (?:great|strong)|felt (?:great|strong)|very strong|strong session|"
                        r"felt good|feeling good", re.I)
# "not feeling strong" contains "feeling strong", so a plainly tired note matched both lists
# and went to review as conflicting. A strong phrase right after a negation is a low one.
NEGATED = re.compile(r"(?:\bnot|\bnever|n't)(?:\s+\w+){0,2}\s+$", re.I)

def energy_cues(text):
    """(low, high) from a note's wording. A high phrase within two words after "not",
    "never" or "-n't" counts as low instead."""
    lo, hi = bool(ENERGY_LOW.search(text)), False
    for m in ENERGY_HIGH.finditer(text):
        if NEGATED.search(text[max(0, m.start() - 30):m.start()]): lo = True
        else: hi = True
    return lo, hi

# Inter-set recovery reads separately from systemic energy - see db.py.
RECOV_POOR = re.compile(r"recovery[^.]{0,25}(hard|slow|poor|rough)|"
                        r"(longer|more time|extra time)[^.]{0,20}between[^.]{0,15}sets|"
                        r"needed?[^.]{0,20}rest[^.]{0,20}between", re.I)

def parse_subjective(con):
    rows = con.execute(
        "SELECT start_date, note FROM annotation WHERE source='health-context.txt' ORDER BY start_date"
    ).fetchall()
    byday = {}
    for r in rows:
        byday.setdefault(r["start_date"], []).append(r["note"] or "")
    written = 0
    for day, notes in byday.items():
        text = " ".join(notes)
        existing = con.execute("SELECT source FROM checkin WHERE date=?", (day,)).fetchone()
        if existing and existing["source"] == "manual":
            continue                      # never clobber something the user typed
        back = None
        if BACK_SPASM.search(text):   back = "spasm"
        elif BACK_SORE.search(text):  back = "sore"
        elif BACK_TIRED.search(text): back = "tired"
        lo, hi = energy_cues(text)
        recov = 1 if RECOV_POOR.search(text) else None
        energy, review = None, 0
        # Being sick is the lowest energy there is. Without this, "stomach bug, sick all day"
        # left the feel signal empty and the day could still come out as a hard session.
        sick = bool(core.SICK.search(text))
        if (lo or sick) and hi: review = 1  # conflicting - ask, don't guess
        elif sick:     energy = 0
        elif lo:       energy = 1
        elif hi:       energy = 3
        if back is None and energy is None and recov is None and not review:
            continue
        con.execute(
            """INSERT INTO checkin (date, back, soreness, energy, inter_set_recovery, note, source,
                                    needs_review, back_reported)
               VALUES (?,?,?,?,?,?,'derived',?,?)
               ON CONFLICT(date) DO UPDATE SET
                 back=excluded.back, back_reported=excluded.back_reported,
                 energy=COALESCE(excluded.energy, checkin.energy),
                 inter_set_recovery=COALESCE(excluded.inter_set_recovery, checkin.inter_set_recovery),
                 note=excluded.note, source='derived',
                 needs_review=excluded.needs_review""",
            (day, back or "ok", None, energy, recov, text[:400], review, 1 if back else 0))
        written += 1
    print(f"   {'subjective':14s} {written:>5} derived check-ins")


if __name__ == "__main__":
    run()

"""Early-access requests from the landing page, kept for the owner.

Other people's names and emails, so they live in one SQLite file on the server's volume
($ESTECOACH_SIGNUPS), never in the code repo or the data repo, and only a person whose config
has `[site] review_signups = true` can read them (/admin/signups). No health details are
asked for: a sign-up list is a contact list.

Each new request can send the owner an email, through Resend ($RESEND_API_KEY) or Postmark
($POSTMARK_TOKEN), to $ESTECOACH_NOTIFY_TO from $ESTECOACH_NOTIFY_FROM. With no key it is only
logged, and the log line never carries the address.
"""
import csv, io, json, logging, os, re, sqlite3, threading, time, urllib.request
from datetime import date, datetime
from pathlib import Path

from estecoach import config

# What people track with today, by kind: the form's checkboxes. Names only, no logos.
TRACK_GROUPS = (
    ("Watch, ring and sleep", ("Apple Watch", "Whoop", "Oura Ring", "Garmin", "Fitbit", "Polar",
                               "Coros", "Samsung Galaxy Watch", "Eight Sleep")),
    ("Lifting", ("Hevy", "Strong", "Jefit", "Fitbod", "Boostcamp", "TrainHeroic", "RP Hypertrophy",
                 "Notes app or spreadsheet", "Paper log")),
    ("Food", ("Cronometer", "MyFitnessPal", "MacroFactor", "Lose It", "Carbon Diet Coach", "Noom")),
    ("Scales and other", ("Smart scale", "Regular scale", "Other")),
)
TRACKS = tuple(t for _, ts in TRACK_GROUPS for t in ts)
LIMITS = {"name": 80, "email": 120, "note": 300}
MAX_BODY = 8 * 1024                # a filled-in form is well under 1 KB
PER_HOUR = 5                       # requests from one address; a person needs one
_EMAIL = re.compile(r"[^@\s]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_recent = {}                       # address -> times of its recent requests, this process only
log = logging.getLogger("estecoach")
# Resend sits behind Cloudflare, which refuses Python's own User-Agent (error 1010, a 403)
# before the key is even read: the first live sign-up's email failed that way.
USER_AGENT = "estecoach/1 (+https://estecoach.app)"


class Invalid(ValueError):
    pass


def path():
    return Path(config.env("SIGNUPS") or Path.home() / ".local/share/estecoach/signups.sqlite")


def connect():
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS signup (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, updated_at TEXT, name TEXT,
        email TEXT NOT NULL UNIQUE, tracks TEXT, note TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS visit (date TEXT PRIMARY KEY, n INT NOT NULL)")
    return con


def clean(form):
    """A form (a dict of lists, as parsed) as a row, or Invalid with what to fix."""
    one = lambda k: ((form.get(k) or [""])[0] or "").strip()
    email = one("email")[:LIMITS["email"]].lower()
    if not _EMAIL.fullmatch(email):
        raise Invalid("That email doesn't look right.")
    tracks = [t for t in TRACKS if t in (form.get("tracks") or [])]
    return {"name": one("name")[:LIMITS["name"]] or None, "email": email,
            "tracks": ", ".join(tracks) or None, "note": one("note")[:LIMITS["note"]] or None}


# Requests that get past the honeypot come from real browsers, often people paid to fill in
# contact forms. Their tells: every box ticked, a note that names this site or carries a link
# (someone asking to try it has no reason to), and an SEO pitch. A real request ticks a few.
MOST_TRACKS = 15
_PITCH = re.compile(r"https?://|www\.|\b(estecoach|estebot)\.app\b|\bseo\b|google (1st|first) page|"
                    r"\brankings?\b|\bbacklinks?\b|price list", re.I)


def spam(row):
    """Why a cleaned row looks like a form-filler rather than a person, or None."""
    if row["tracks"] and row["tracks"].count(", ") + 1 >= MOST_TRACKS:
        return "most boxes ticked"
    if row["note"] and _PITCH.search(row["note"]):
        return "a link or a pitch in the note"
    return None


def allowed(address, now=None):
    """At most PER_HOUR requests an hour from one address."""
    now = now or time.time()
    times = [t for t in _recent.get(address, []) if now - t < 3600]
    if len(times) >= PER_HOUR:
        _recent[address] = times
        return False
    _recent[address] = times + [now]
    return True


def save(row):
    """Insert, or update the row already there for this email. -> True if it was new."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        new = con.execute("SELECT 1 FROM signup WHERE email=?", (row["email"],)).fetchone() is None
        con.execute("""INSERT INTO signup (at, name, email, tracks, note) VALUES (?,?,?,?,?)
                       ON CONFLICT(email) DO UPDATE SET updated_at=?, name=COALESCE(excluded.name, name),
                         tracks=COALESCE(excluded.tracks, tracks), note=COALESCE(excluded.note, note)""",
                    (now, row["name"], row["email"], row["tracks"], row["note"], now))
    return new


def listing():
    if not path().exists():
        return []
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM signup ORDER BY id DESC")]


def count():
    if not path().exists():
        return 0
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM signup").fetchone()[0]


def delete(sid):
    with connect() as con:
        con.execute("DELETE FROM signup WHERE id=?", (sid,))


def as_csv():
    out = io.StringIO()
    w = csv.writer(out)
    cols = ("id", "at", "updated_at", "name", "email", "tracks", "note")
    w.writerow(cols)
    for r in reversed(listing()):
        w.writerow([r[k] for k in cols])
    return out.getvalue()


def count_visit(day=None):
    """One more view of the landing page today: a count kept on the server, no cookies."""
    try:
        with connect() as con:
            con.execute("INSERT INTO visit VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET n = n + 1",
                        (day or date.today().isoformat(),))
    except sqlite3.Error:
        pass                                       # a count is never worth a failed page


def visits(days=14):
    if not path().exists():
        return []
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT date, n FROM visit ORDER BY date DESC LIMIT ?", (days,))]


# ---------------------------------------------------------------- telling the owner
def _send(subject, text):
    to, sender = config.env("NOTIFY_TO"), config.env("NOTIFY_FROM")
    if not to or not sender:
        return "no NOTIFY_TO/NOTIFY_FROM"
    if os.environ.get("RESEND_API_KEY"):
        req = urllib.request.Request("https://api.resend.com/emails", method="POST",
            data=json.dumps({"from": sender, "to": [to], "subject": subject, "text": text}).encode(),
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json",
                     "User-Agent": USER_AGENT})
    elif os.environ.get("POSTMARK_TOKEN"):
        req = urllib.request.Request("https://api.postmarkapp.com/email", method="POST",
            data=json.dumps({"From": sender, "To": to, "Subject": subject, "TextBody": text}).encode(),
            headers={"X-Postmark-Server-Token": os.environ["POSTMARK_TOKEN"],
                     "Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})
    else:
        return "no email key"
    with urllib.request.urlopen(req, timeout=15) as r:
        return f"sent ({r.status})"


def notify(row, new):
    """Tell the owner, in the background so the form never waits on an email service."""
    what = "new" if new else "updated"
    def run():
        try:
            how = _send(f"EsteCoach early access: {row['name'] or row['email']}",
                        f"{row['name'] or '(no name)'} <{row['email']}>\nTracks: {row['tracks'] or '-'}\n\n"
                        f"{row['note'] or ''}\n\nAll requests: /admin/signups")
        except Exception as e:                     # never let a notification break a sign-up
            how = f"email failed: {type(e).__name__}" + (f" {e.code}" if hasattr(e, "code") else "")
        log.warning("sign-up %s (%s)", what, how)    # no name or address in the log
    threading.Thread(target=run, daemon=True).start()

"""Requests to use EsteCoach, from the demo's sign-up form, kept for the owner to review.

Other people's names and emails, so they live in one SQLite file on the server's volume
($ESTECOACH_SIGNUPS), never in the code repo or the data repo, and only a person whose config
has `[site] review_signups = true` can read them (web/main.py).
"""
import os, re, sqlite3, time
from datetime import datetime
from pathlib import Path

from estecoach import config

# Name, email and one optional answer. No health details: a sign-up list is not a place for
# them, and asking for none keeps it a plain contact list.
LIMITS = {"name": 80, "email": 120, "tracks": 300}
PER_HOUR = 5                       # requests from one address; a person needs one
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_recent = {}                       # address -> times of its recent requests, this process only


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
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL,
        tracks TEXT, reviewed_at TEXT)""")
    return con


def clean(form):
    """The form as a row, or Invalid with what to fix. Everything is trimmed and capped."""
    name = (form.get("name") or "").strip()[:LIMITS["name"]]
    email = (form.get("email") or "").strip()[:LIMITS["email"]]
    if not name:
        raise Invalid("Add your name.")
    if not _EMAIL.fullmatch(email):
        raise Invalid("That email doesn't look right.")
    return {"name": name, "email": email,
            "tracks": (form.get("tracks") or "").strip()[:LIMITS["tracks"]] or None}


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
    with connect() as con:
        con.execute("INSERT INTO signup (at, name, email, tracks) VALUES (?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), row["name"], row["email"], row["tracks"]))


def listing():
    if not path().exists():
        return []
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM signup ORDER BY id DESC")]


def unreviewed():
    if not path().exists():
        return 0
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM signup WHERE reviewed_at IS NULL").fetchone()[0]


def mark_reviewed(sid):
    with connect() as con:
        con.execute("UPDATE signup SET reviewed_at=? WHERE id=? AND reviewed_at IS NULL",
                    (datetime.now().isoformat(timespec="seconds"), sid))

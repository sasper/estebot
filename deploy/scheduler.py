"""Run the daily sync for every signed-in person at each SYNC_AT time (local time, $TZ), forever.

SYNC_AT is one time or a comma-separated list, "04:30,12:00": the early run brings in
yesterday's food and workouts before the morning's call, and the noon run is the backstop.
Started in the background by entrypoint.sh in private mode. A missed run isn't caught up:
the machine runs all the time, and a phone upload triggers its own feed sync anyway.
"""
import os, subprocess, sys, time, tomllib
from datetime import datetime, timedelta

def times(spec):
    """'04:30, 12:00' -> ['04:30', '12:00']. A bad entry fails at start, not at 4 a.m."""
    out = [t.strip() for t in spec.split(",") if t.strip()]
    for t in out:
        h, m = map(int, t.split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(f"SYNC_AT: {t!r} isn't a time of day")
    if not out:
        raise ValueError("SYNC_AT is empty")
    return out

AT = times(os.environ.get("SYNC_AT", "12:00"))

def configs():
    with open(os.environ["ESTEBOT_USERS"], "rb") as fh:
        return sorted(set(tomllib.load(fh).get("users", {}).values()))

def seconds_until(hhmm, now):
    h, m = map(int, hhmm.split(":"))
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()

def seconds_until_next(at, now):
    """Seconds to the soonest of the times in `at`."""
    return min(seconds_until(t, now) for t in at)

if __name__ == "__main__":
    while True:
        time.sleep(seconds_until_next(AT, datetime.now()))
        for cfg in configs():
            print(f"== sync {cfg}", flush=True)
            subprocess.run([sys.executable, "-m", "estebot.daily"],
                           env={**os.environ, "ESTEBOT_CONFIG": cfg})

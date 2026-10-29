"""Run the daily sync for every signed-in person at SYNC_AT (local time, $TZ), forever.

Started in the background by entrypoint.sh in private mode. A missed run isn't caught up:
the machine runs all the time, and a phone upload triggers its own feed sync anyway.
"""
import os, subprocess, sys, time, tomllib
from datetime import datetime, timedelta

AT = os.environ.get("SYNC_AT", "12:00")

def configs():
    with open(os.environ["ESTEBOT_USERS"], "rb") as fh:
        return sorted(set(tomllib.load(fh).get("users", {}).values()))

def seconds_until(hhmm, now):
    h, m = map(int, hhmm.split(":"))
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()

if __name__ == "__main__":
    while True:
        time.sleep(seconds_until(AT, datetime.now()))
        for cfg in configs():
            print(f"== sync {cfg}", flush=True)
            subprocess.run([sys.executable, "-m", "estebot.daily"],
                           env={**os.environ, "ESTEBOT_CONFIG": cfg})

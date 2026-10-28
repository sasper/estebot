#!/bin/sh
# Start the public demo. Each start writes a new made-up person ending today, loads it into
# the app's database and serves it. Both steps take well under a second, so a machine that
# was stopped while idle comes back with current data and nothing has to be kept.
set -eu

rm -rf "$ESTEBOT_DEMO_DIR"
python -m estebot.demo --out "$ESTEBOT_DEMO_DIR" --end today
python -m estebot.web.ingest

exec uvicorn estebot.web.main:app --host 0.0.0.0 --port "$PORT" \
    --proxy-headers --forwarded-allow-ips '*' --no-server-header

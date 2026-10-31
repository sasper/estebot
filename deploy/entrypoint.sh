#!/bin/sh
# Two modes.
#
# Demo only (no secrets set): write a made-up person ending today, load it, serve it
# read-only. Anyone can deploy the repo this way.
#
# Private (ESTEBOT_USERS_TOML set, as a Fly secret, base64-encoded like the other multi-line ones): the same demo for visitors, plus the
# signed-in people's own pages. Their data repo is cloned onto the volume over SSH with a
# deploy key, each config is loaded, and the daily sync runs at each SYNC_AT time and pushes back.
set -eu

# Secrets that span lines or may hold any character arrive base64-encoded (NAME_B64).
for name in ESTEBOT_USERS_TOML DATA_REPO_KEY CRONOMETER_PASSWORD; do
    eval "v=\${${name}_B64:-}"
    [ -n "$v" ] && export "$name=$(printf '%s' "$v" | base64 -d)"
done

# The made-up athlete's last 90 recorded calls, each from the data before its day. A second
# or two, before the server starts so it never competes with a request; a failure leaves
# the column empty rather than the site down.
backfill_demo() {
    python -m estebot.analysis.calls --backfill 90 >/dev/null || echo "demo backfill failed; serving without it"
}

rm -rf "$ESTEBOT_DEMO_DIR"
python -m estebot.demo --out "$ESTEBOT_DEMO_DIR" --end today

if [ -n "${ESTEBOT_USERS_TOML:-}" ]; then
    unset ESTEBOT_DEMO                       # the demo person is read-only by itself now
    export ESTEBOT_USERS=/data/users.toml
    printf '%s\n' "$ESTEBOT_USERS_TOML" > "$ESTEBOT_USERS"
    chmod 600 "$ESTEBOT_USERS"

    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    printf '%s\n' "$DATA_REPO_KEY" > "$HOME/.ssh/id_ed25519" && chmod 600 "$HOME/.ssh/id_ed25519"
    cp /app/github_known_hosts "$HOME/.ssh/known_hosts"
    export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=yes"
    git config --global user.name "EsteBot sync"
    git config --global user.email "sync@estebot.app"
    if [ -d "$DATA_DIR/.git" ]; then
        git -C "$DATA_DIR" pull -q --rebase --autostash || echo "data repo pull failed; serving what's there"
    else
        git clone -q "$DATA_REPO" "$DATA_DIR"
    fi

    python -m estebot.web.ingest              # the demo (ESTEBOT_CONFIG points at it)
    backfill_demo
    for cfg in $(python -c 'import os, tomllib; print(*sorted(set(tomllib.load(open(os.environ["ESTEBOT_USERS"], "rb")).get("users", {}).values())))'); do
        ESTEBOT_CONFIG="$cfg" python -m estebot.web.ingest
    done
    python /app/scheduler.py &
else
    python -m estebot.web.ingest
    backfill_demo
fi

# No access log: the sign-in callback's URL carries a one-time code.
exec uvicorn estebot.web.main:app --host 0.0.0.0 --port "$PORT" \
    --proxy-headers --forwarded-allow-ips '*' --no-server-header --no-access-log

#!/usr/bin/env python3
"""Run the EsteBot page as a launchd service, from the same origin/main checkout as the sync.

    python -m estebot.web.service --install     start at login, restart if it dies, and
                                                check every 15 min for newly merged code
    python -m estebot.web.service --restart     refresh to origin/main now and restart
                                                (git pull the main checkout first, or this module may not exist there yet)
    python -m estebot.web.service --uninstall

Serves http://127.0.0.1:8742. Uvicorn loads the code once at startup, so a server started
by hand keeps showing whatever code it started with; one ran from 12 to 15 Oct showing a
page several merged PRs out of date. This one serves only merged code (the daily sync's
runner worktree), and the noon sync restarts it right after refreshing that checkout, so a
merge shows by the next noon at the latest - or at once with --restart. A second job
(com.estebot.web-update) checks origin/main every 15 minutes and restarts the page when
something new has merged, so the noon restart is a backstop rather than the only path.
Data needs no restart: every request reads the database.
"""
import os, plistlib, subprocess, sys, time
from pathlib import Path

LABEL = "com.estebot.web"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / "Library/Logs/estebot-web.log"
HOST, PORT = "127.0.0.1", 8742
UPDATE_LABEL = "com.estebot.web-update"
UPDATE_PLIST = Path.home() / "Library/LaunchAgents" / f"{UPDATE_LABEL}.plist"
UPDATE_EVERY_S = 15 * 60


def plist(runner, main, python):
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "uvicorn", "estebot.web.main:app",
                             "--host", HOST, "--port", str(PORT)],
        "WorkingDirectory": str(runner),
        "EnvironmentVariables": {"PYTHONPATH": str(runner / "src"),
                                 "ESTEBOT_CONFIG": str(main / "estebot.toml"),
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
        "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG),
    }


def update_script(runner, uid):
    """Shell, not Python, on purpose: it must work before the checkout holds any new code.
    Does nothing unless origin/main moved, and never moves the checkout under a running
    daily sync (which reads the same files)."""
    daily = f"gui/{uid}/com.estebot.daily"
    web = f"gui/{uid}/{LABEL}"
    return (f"cd '{runner}' && git fetch -q origin || exit 0; "
            f"[ \"$(git rev-parse HEAD)\" = \"$(git rev-parse origin/main)\" ] && exit 0; "
            f"launchctl print {daily} 2>/dev/null | grep -q 'state = running' && exit 0; "
            f"git checkout -q --detach origin/main && launchctl kickstart -k {web} && "
            f"echo \"$(date '+%F %T') web-update: restarted on $(git rev-parse --short HEAD)\"")


def update_plist(runner, uid):
    return {
        "Label": UPDATE_LABEL,
        "ProgramArguments": ["/bin/sh", "-c", update_script(runner, uid)],
        "StartInterval": UPDATE_EVERY_S, "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
        "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG),
    }


def reload(label, plist_path, timeout_s=15):
    """bootout, wait until launchd has really dropped the job, then bootstrap. bootout
    returns before the job is gone, and a bootstrap in that window fails with 'Input/output
    error' (17 Oct 2026: that took the page down mid-reinstall)."""
    target = f"gui/{os.getuid()}/{label}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True)
    deadline = time.monotonic() + timeout_s
    while subprocess.run(["launchctl", "print", target], capture_output=True).returncode == 0:
        if time.monotonic() > deadline:
            raise RuntimeError(f"{label} still loaded {timeout_s}s after bootout")
        time.sleep(0.3)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)], check=True)


def _target():
    return f"gui/{os.getuid()}/{LABEL}"


def install():
    from estebot import daily
    main = daily._main_checkout()
    daily.ensure_runner(main)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist(daily.RUNNER, main, sys.executable)))
    reload(LABEL, PLIST)
    print(f"installed {PLIST}: http://{HOST}:{PORT} from {daily.RUNNER} (origin/main), log {LOG}")
    UPDATE_PLIST.write_bytes(plistlib.dumps(update_plist(daily.RUNNER, os.getuid())))
    reload(UPDATE_LABEL, UPDATE_PLIST)
    print(f"installed {UPDATE_PLIST}: restarts the page within {UPDATE_EVERY_S // 60} min of a merge")


def restart(refresh=True):
    """-> a one-line status. refresh=True first moves the checkout to origin/main."""
    if not PLIST.exists():
        return "not installed (python -m estebot.web.service --install)"
    if refresh:
        from estebot import daily
        subprocess.run(["git", "-C", str(daily.RUNNER), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(daily.RUNNER), "checkout", "-q", "--detach",
                        "origin/main"], check=True)
    r = subprocess.run(["launchctl", "kickstart", "-k", _target()], capture_output=True, text=True)
    return "restarted" if r.returncode == 0 else f"restart failed: {r.stderr.strip()}"


def uninstall():
    for label, plist_path in ((LABEL, PLIST), (UPDATE_LABEL, UPDATE_PLIST)):
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
        plist_path.unlink(missing_ok=True)
        print(f"removed {plist_path}")


if __name__ == "__main__":
    if "--install" in sys.argv: install()
    elif "--restart" in sys.argv: print(restart())
    elif "--uninstall" in sys.argv: uninstall()
    else: print(__doc__)

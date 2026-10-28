#!/usr/bin/env python3
"""Daily unattended sync: Hevy, notes, lifting tables, Apple Health feed, web cache, commit.

    python -m estebot.daily              run once now
    python -m estebot.daily --install    schedule it daily via launchd (default 12:00)
                                         and create the runner checkout (below)
    python -m estebot.daily --uninstall

Runs on the Mac, not in the cloud, because it needs the Keychain (Hevy key), iCloud Drive
(notes) and the data repo. launchd runs a missed job when the machine wakes.

Apple Health arrives via the phone app's daily files (health_auto_export). The full
export.xml stays a weekly manual step. Cronometer is pulled from its website (unofficial, so
that step is optional: if it breaks, nutrition is rebuilt from the files already there and
the rest of the sync still commits). Deliberately NOT here: the ledger and context log
(judgement, done at the weekly sync). The commit takes only tidy/ and
health-context.txt, both derived or copied, so an authored file being edited is never swept
into an automatic commit.
"""
import os, plistlib, subprocess, sys
from datetime import date
from pathlib import Path

LABEL = "com.estebot.daily"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / "Library/Logs/estebot-daily.log"
REPO = Path(__file__).resolve().parents[2]          # the checkout this file runs from
# The scheduled job runs from its own git worktree, pinned to origin/main and refreshed
# before every run, never from the working checkout. Branches get checked out there all
# day; a sync that ran whatever happened to be checked out at noon would run unmerged code.
RUNNER = Path.home() / ".local/share/estebot/daily"


def _main_checkout():
    """The primary checkout, which holds the untracked estebot.toml, even when called from
    the runner worktree."""
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip()).parent

# (name, args, required). A failed optional step notifies and the sync carries on.
STEPS = [
    ("hevy", ["-m", "estebot.parsers.hevy"], True),
    ("notes", ["-m", "estebot.parsers.notes"], True),
    # Health before lifting: pull-up e1RM uses the weight trend, so today's weigh-in goes first.
    ("apple health feed", ["-m", "estebot.parsers.health_auto_export"], True),
    ("lifting tables", ["-m", "estebot.parsers.jefit"], True),
    ("cronometer pull", ["-m", "estebot.parsers.cronometer_pull"], False),
    ("nutrition", ["-m", "estebot.parsers.cronometer"], True),
    ("web cache", ["-m", "estebot.web.ingest"], True),
]


def _notify(msg):
    # Only on failure. A success notification every day would train you to ignore it.
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "EsteBot daily sync"'],
                   capture_output=True)


def run():
    from estebot import config
    data = config.load().data_dir
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print(f"== {date.today().isoformat()} daily sync, code {head or '?'} from {REPO}")
    # The checkout was just refreshed to origin/main; restart the page so it serves the
    # same code. No-op if the web service isn't installed.
    from estebot.web import service
    print(f"-- web app: {service.restart(refresh=False)}")
    for name, args, required in STEPS:
        r = subprocess.run([sys.executable, *args], cwd=REPO, env=env,
                           capture_output=True, text=True)
        print(f"-- {name}: exit {r.returncode}")
        print((r.stdout + r.stderr).rstrip())
        if r.returncode != 0:
            _notify(f"{name} failed - see {LOG}")
            if required:
                return r.returncode

    paths = ["tidy", "health-context.txt"]
    git = ["git", "-C", str(data)]
    subprocess.run(git + ["add", "--", *paths], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        print("-- commit: nothing changed")
        return 0
    # Pathspec form commits only these paths, even if something else is staged.
    r = subprocess.run(git + ["commit", "-q", "-m", f"Daily sync {date.today().isoformat()}",
                              "--", *paths], capture_output=True, text=True)
    print(f"-- commit: exit {r.returncode} {r.stderr.strip()}")
    if r.returncode != 0:
        _notify("commit failed - see log")
    return r.returncode


def ensure_runner(main):
    """Create the origin/main worktree both launchd jobs run from, if it isn't there."""
    if not RUNNER.exists():
        RUNNER.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(main), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--detach",
                        str(RUNNER), "origin/main"], check=True)


def install(hour=12, minute=0):
    main = _main_checkout()
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ensure_runner(main)
    # Refresh to origin/main, then run. If the fetch fails (offline), the last fetched main
    # still runs: a day of slightly old code beats a day with no sync.
    refresh = (f"git -C '{RUNNER}' fetch -q origin && "
               f"git -C '{RUNNER}' checkout -q --detach origin/main; "
               f"exec '{sys.executable}' -m estebot.daily")
    PLIST.write_bytes(plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", "-c", refresh],
        "WorkingDirectory": str(RUNNER),
        "EnvironmentVariables": {"PYTHONPATH": str(RUNNER / "src"),
                                 "ESTEBOT_CONFIG": str(main / "estebot.toml"),
                                 "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG),
    }))
    from estebot.web.service import reload
    reload(LABEL, PLIST)
    print(f"installed {PLIST}: daily at {hour:02d}:{minute:02d} from {RUNNER} (origin/main), log {LOG}")


def uninstall():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print(f"removed {PLIST}")
    if RUNNER.exists():
        subprocess.run(["git", "-C", str(_main_checkout()), "worktree", "remove", "--force",
                        str(RUNNER)], check=True)
        print(f"removed {RUNNER}")


if __name__ == "__main__":
    if "--install" in sys.argv: install()
    elif "--uninstall" in sys.argv: uninstall()
    else: sys.exit(run())

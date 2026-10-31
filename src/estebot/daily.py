#!/usr/bin/env python3
"""Daily unattended sync: Hevy, notes, lifting tables, Apple Health feed, web cache, commit.

    python -m estebot.daily              run once now
    python -m estebot.daily --feed       only what a new Health Auto Export upload changes
    python -m estebot.daily --install    schedule it daily via launchd (default 12:00)
                                         and create the runner checkout (below)
    python -m estebot.daily --uninstall

Runs on the Mac under launchd (which runs a missed job when the machine wakes), or on a
server. There the secrets come from the environment, the phone uploads instead of writing to
iCloud, there are no iCloud notes, and [sync] in the config has it pull the data repo first
and push it after:

    [sync]
    pull = true
    push = true
    keep_feed_days = 3          # phone uploads are ~30 MB a day and stay out of git

Apple Health arrives via the phone app's daily files (health_auto_export). The full
export.xml stays a weekly manual step. Cronometer is pulled from its website (unofficial, so
that step is optional: if it breaks, nutrition is rebuilt from the files already there and
the rest of the sync still commits). Deliberately NOT here: the ledger and context log
(judgement, done at the weekly sync). The commit takes only tidy/ and
health-context.txt, both derived or copied, so an authored file being edited is never swept
into an automatic commit.
"""
import fcntl, os, plistlib, shutil, subprocess, sys
from datetime import date, datetime
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
# The notes step only runs where a notes source is configured (not on a server).
STEPS = [
    ("hevy", ["-m", "estebot.parsers.hevy"], True),
    ("notes", ["-m", "estebot.parsers.notes"], True),
    # Health before lifting: pull-up e1RM uses the weight trend, so today's weigh-in goes first.
    ("apple health feed", ["-m", "estebot.parsers.health_auto_export"], True),
    ("lifting tables", ["-m", "estebot.parsers.jefit"], True),
    ("cronometer pull", ["-m", "estebot.parsers.cronometer_pull"], False),
    ("nutrition", ["-m", "estebot.parsers.cronometer"], True),
    ("web cache", ["-m", "estebot.web.ingest"], True),
    # Last: it reads the cache just refreshed. Optional, since a missed day is only a gap.
    ("readiness call", ["-m", "estebot.analysis.calls"], False),
]
# After a phone upload: the feed, the tables that read its weigh-ins, and the page's cache.
# The call too: the morning's first upload brings the night's sleep, so that is when it's made.
FEED_STEPS = ("apple health feed", "lifting tables", "web cache", "readiness call")
PENDING = ".feed-pending"      # written by each upload (web/main.py), taken by the run that reads it


def _notify(msg):
    # Only on failure. A success notification every day would train you to ignore it.
    if not shutil.which("osascript"):
        return                          # not a Mac: the log is the record
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "EsteBot daily sync"'],
                   capture_output=True)


def _prune_feed(cfg, keep_days):
    """Delete phone-upload files older than keep_days. They're about 30 MB a day; what they
    say is already in the tidy tables, which are committed. Only files inside the data
    directory are touched (on a Mac the feed is in iCloud and this never runs)."""
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    src = cfg.sources.get("health_auto_export", {})
    for key in ("metrics_glob", "workouts_glob"):
        folder = Path(os.path.expanduser(src.get(key, ""))).parent
        if not src.get(key) or not folder.is_relative_to(cfg.data_dir) or not folder.is_dir():
            continue
        for f in folder.glob("HealthAutoExport-*.json"):
            if f.stem.removeprefix("HealthAutoExport-") < cutoff:
                f.unlink()
                print(f"-- pruned {f.name}")


def run(feed_only=False):
    from estebot import config
    cfg = config.load()
    data = cfg.data_dir
    # One sync at a time per data repo: a phone upload can land mid-way through the noon run,
    # and two runs rewriting the same CSVs would interleave.
    with open(data / ".sync.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Each upload leaves PENDING and starts a feed run, and the runs queue on the lock; a
        # burst of uploads would parse the same files once per upload. The run that gets the
        # lock takes the marker before reading anything, so a later run finding none has
        # nothing an earlier one didn't read. A full sync runs the feed step too. A run that
        # fails puts the marker back, so the next one in the queue tries again (a pull that
        # failed on a network blip used to be retried by the next upload's run).
        pending = data / PENDING
        if feed_only and not pending.exists():
            print(f"== {datetime.now().isoformat(sep=' ', timespec='minutes')} feed upload: "
                  "already read by the run before")
            return 0
        pending.unlink(missing_ok=True)
        rc = 1
        try:
            rc = _run(cfg, feed_only)
            return rc
        finally:
            if rc != 0:
                pending.touch()


def _run(cfg, feed_only):
    data = cfg.data_dir
    git = ["git", "-C", str(data)]
    if cfg.sync.get("pull"):
        r = subprocess.run(git + ["pull", "-q", "--rebase", "--autostash"], capture_output=True, text=True)
        print(f"-- pull: exit {r.returncode} {r.stderr.strip()}")
        if r.returncode != 0:
            return r.returncode         # building on a repo that didn't update would diverge
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    kind = "feed upload" if feed_only else "daily sync"
    # The time too: when the morning's first upload lands decides when the day's call is made.
    print(f"== {datetime.now().isoformat(sep=' ', timespec='minutes')} {kind}, code {head or '?'} from {REPO}")
    if not feed_only:
        # The checkout was just refreshed to origin/main; restart the page so it serves the
        # same code. No-op if the web service isn't installed (always, on a server).
        from estebot.web import service
        print(f"-- web app: {service.restart(refresh=False)}")
    for name, args, required in STEPS:
        if feed_only and name not in FEED_STEPS:
            continue
        if name == "notes" and "notes" not in cfg.sources:
            continue
        r = subprocess.run([sys.executable, *args], cwd=REPO, env=env,
                           capture_output=True, text=True)
        print(f"-- {name}: exit {r.returncode}")
        print((r.stdout + r.stderr).rstrip())
        if r.returncode != 0:
            _notify(f"{name} failed - see {LOG}")
            if required:
                return r.returncode

    if cfg.sync.get("keep_feed_days"):
        _prune_feed(cfg, int(cfg.sync["keep_feed_days"]))
    # raw/cronometer too: the tables are rebuilt from it, so a copy that's missing the
    # server's latest pull rebuilds a different day (seen on the first server sync).
    paths = [p for p in ("tidy", "health-context.txt", "raw/cronometer", "calls.csv") if (data / p).exists()]
    subprocess.run(git + ["add", "--", *paths], check=True)
    if subprocess.run(git + ["diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        print("-- commit: nothing changed")
    else:
        # Pathspec form commits only these paths, even if something else is staged.
        r = subprocess.run(git + ["commit", "-q", "-m", f"{kind.capitalize()} {date.today().isoformat()}",
                                  "--", *paths], capture_output=True, text=True)
        print(f"-- commit: exit {r.returncode} {r.stderr.strip()}")
        if r.returncode != 0:
            _notify("commit failed - see log")
            return r.returncode
    if cfg.sync.get("push"):            # also clears anything an earlier run couldn't push
        r = subprocess.run(git + ["push", "-q", "origin", "HEAD"], capture_output=True, text=True)
        print(f"-- push: exit {r.returncode} {r.stderr.strip()}")
        return r.returncode
    return 0


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
    else: sys.exit(run(feed_only="--feed" in sys.argv))

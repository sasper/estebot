#!/usr/bin/env python3
"""Cronometer web export -> raw snapshot, so nutrition no longer needs a manual download.

Cronometer has no public API, and its Apple Health sync only runs when the iPhone app does,
which on an Android-logged diary missed more than half the days since 3 Sep 2026. This
logs in as the website does and downloads the same Daily Summary CSV the Export button
gives, for the last WINDOW_DAYS days, into data_dir/raw/cronometer/. parsers/cronometer.py
merges those files with any manual exports, newest file winning per day.

Unofficial on purpose (the owner accepted the terms-of-service risk). If Cronometer changes
its site this fails loudly and the manual export still works.

The login is read from the Keychain, never a file:
  security add-generic-password -U -s estebot-cronometer -a <email> -w
"""
import http.cookiejar, json, re, subprocess, urllib.parse, urllib.request
from datetime import date, timedelta

SITE = "https://cronometer.com"
WINDOW_DAYS = 14        # re-pull a window so edits and late "Completed" ticks are picked up
UA = "Mozilla/5.0"


class PullError(RuntimeError):
    pass


# ---------------------------------------------------------------- credentials
def credentials(cfg):
    service = cfg.sources.get("cronometer", {}).get("keychain_service", "estebot-cronometer")
    try:
        attrs = subprocess.run(["security", "find-generic-password", "-s", service],
                               capture_output=True, text=True, check=True).stdout
        pw = subprocess.run(["security", "find-generic-password", "-w", "-s", service],
                            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit("No Cronometer login in the Keychain. Add it with:\n"
                         f"  security add-generic-password -U -s {service} -a <email> -w") from e
    m = re.search(r'"acct"<blob>="([^"]*)"', attrs)
    if not m:
        raise SystemExit(f"Keychain entry {service} has no account (email) set")
    return m.group(1), pw


# ---------------------------------------------------------------- session (pure parts first)
def anticsrf(html):
    m = re.search(r'name="anticsrf"\s+value="([^"]+)"', html)
    if not m:
        raise PullError("login page has no anticsrf token; the site has changed")
    return m.group(1)

def check_login(body):
    """The login POST answers JSON: {"redirect": ...} on success, {"error": ...} otherwise."""
    res = json.loads(body)
    if res.get("error"):
        raise PullError(f"login refused: {res['error']}")
    return res

def session():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar

def _req(opener, path, data=None):
    req = urllib.request.Request(SITE + path, data=data, headers={"User-Agent": UA})
    with opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8")

def login(opener, user, pw):
    token = anticsrf(_req(opener, "/login/"))
    form = urllib.parse.urlencode({"username": user, "password": pw,
                                   "userCode": "", "anticsrf": token}).encode()
    return check_login(_req(opener, "/login", form))

# ---------------------------------------------------------------- export token (GWT-RPC)
# The export URL needs a one-time token from the web app's RPC service. The build id and
# the service's policy id change when Cronometer redeploys, so both are read from the site
# on every run (the build id changed between 13 and 14 Oct 2026; the policy id did not).
GWT_BASE = SITE + "/cronometer/"
SVC = "com.cronometer.shared.rpc.CronometerService"

def gwt_build(nocache_js):
    m = re.search(r"'([0-9A-F]{32})'", nocache_js)
    if not m:
        raise PullError("no build id in cronometer.nocache.js; the site has changed")
    return m.group(1)

def gwt_policy(build_js):
    m = re.search(r"'app','([0-9A-F]{32})'", build_js)
    if not m:
        raise PullError("no RPC policy id in the app build; the site has changed")
    return m.group(1)

def gwt_ok(body, pattern, what):
    """GWT answers //OK[...] or //EX[...] (an exception, e.g. NotLoggedInException)."""
    if body.startswith("//EX"):
        names = re.findall(r'"([\w.]+Exception)[^"]*"', body)
        raise PullError(f"{what}: server raised {names[0] if names else body[:120]}")
    m = re.search(pattern, body)
    if not m:
        raise PullError(f"{what}: unexpected answer {body[:60]!r}")
    return m.group(1)

def _rpc(opener, build, body):
    req = urllib.request.Request(GWT_BASE + "app", data=body.encode(), headers={
        "User-Agent": UA, "Content-Type": "text/x-gwt-rpc; charset=utf-8",
        "X-GWT-Permutation": build, "X-GWT-Module-Base": GWT_BASE})
    with opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8")

def export_token(opener, jar):
    build = gwt_build(_req(opener, "/cronometer/cronometer.nocache.js"))
    policy = gwt_policy(_req(opener, f"/cronometer/{build}.cache.js"))
    head = f"7|0|{{n}}|{GWT_BASE}|{policy}|{SVC}|"
    # The int is the client's timezone offset; the export takes explicit dates, so it only
    # has to be accepted. authenticate also rotates the sesnonce cookie, so read it after.
    user = gwt_ok(_rpc(opener, build, head.format(n=5) +
                       "authenticate|java.lang.Integer/3438268394|1|2|3|4|1|5|5|-300|"),
                  r"//OK\[(\d+),", "authenticate")
    nonce = next((c.value for c in jar if c.name == "sesnonce"), None)
    if not nonce:
        raise PullError("no sesnonce cookie after authenticate")
    return gwt_ok(_rpc(opener, build, head.format(n=8) +
                       "generateAuthorizationToken|java.lang.String/2004016611|I|"
                       f"com.cronometer.shared.user.AuthScope/2065601159|{nonce}|"
                       f"1|2|3|4|4|5|6|6|7|8|{user}|3600|7|2|"),
                  r'"([0-9a-f]{32})"', "generateAuthorizationToken")

def export_daily_summary(opener, jar, start, end):
    """The Export button's Daily Summary CSV, with the per-meal rows the parser needs."""
    q = urllib.parse.urlencode({"nonce": export_token(opener, jar), "generate": "dailySummary",
                                "start": start.isoformat(), "end": end.isoformat(),
                                "includeMealGroups": "true"})
    body = _req(opener, f"/export?{q}")
    if not body.startswith("Date,"):
        raise PullError(f"export did not return the CSV: {body[:60]!r}")
    return body


# ---------------------------------------------------------------- main
def main():
    from estebot import config
    cfg = config.load()
    end = date.today()
    start = end - timedelta(days=WINDOW_DAYS - 1)
    opener, jar = session()
    login(opener, *credentials(cfg))
    csv_text = export_daily_summary(opener, jar, start, end)
    out = cfg.data_file("raw") / "cronometer" / f"dailysummary-{end.isoformat()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(csv_text)
    print(f"cronometer: {start} -> {end} -> {out}")


if __name__ == "__main__":
    main()

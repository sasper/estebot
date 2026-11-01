"""The landing page at / for visitors: what EsteCoach does, the demo's real call and session,
and the early-access form.

Its pictures are the app's own components (_call.html, _session.html) rendered on the demo
person's data, so they're today's and never a stale screenshot. Building that context is the
expensive part of a page, and the demo only changes once a day, so it is kept for ten
minutes: a link shared on LinkedIn shouldn't rebuild every chart for every visitor.
"""
import time
from datetime import date, timedelta

from estecoach.analysis import engine
from estecoach.web import people, signups

LOOK_BACK = 90


def pick_day(con, goals, today=None):
    """The demo day the hero shows: the most recent one whose call is to train (anything but
    rest) with a real mix behind it, at least two areas flagged and two clear. Failing that,
    one flagged; failing that, today. An all-green card says nothing about how the call
    weighs things; a made-up one would break the rule that every number is computed."""
    today = date.fromisoformat(today or date.today().isoformat())
    days = [(today - timedelta(days=k)).isoformat() for k in range(LOOK_BACK)]
    loose = None
    for d in days:
        r = engine.readiness(con, d, goals)
        if r["verdict"] == "rest":
            continue
        lv = [x["level"] for x in r["signals"]]
        flagged, clear = sum(l in ("amber", "red") for l in lv), sum(l == "green" for l in lv)
        if flagged >= 2 and clear >= 2:
            return d
        if flagged >= 1 and loose is None:
            loose = d
    return loose or days[0]

CACHE_S = 600
_cache = {}


def demo_context(request, context_fn):
    key = date.today().isoformat()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    p = people.demo()
    ctx = context_fn(request, p, pick_day(p.connect(), p.goals)) if p else None
    _cache.clear()
    _cache[key] = (time.time(), ctx)
    return ctx


def page(request, tpl, context_fn, status=200, form=None, error=None, done=None, count=True):
    if count:
        signups.count_visit()
    ctx = demo_context(request, context_fn) or {}
    ask = next(((q["text"], a["text"]) for q, a in zip(ctx.get("chat_history", []), ctx.get("chat_history", [])[1:])
                if q.get("role") == "user" and a.get("role") != "user"), None)
    return tpl.TemplateResponse(request, "landing.html", dict(
        ctx, base="/demo", present=False, waiting={}, waiting_for=[], hevy=None, csrf="",
        ask_first=False, ask=ask, tracks=signups.TRACKS, track_groups=signups.TRACK_GROUPS, form=form or {}, error=error, done=done,
        limits=signups.LIMITS), status_code=status)

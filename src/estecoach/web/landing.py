"""The landing page at / for visitors: what EsteCoach does, the demo's real call and session,
and the early-access form.

Its pictures are the app's own components (_call.html, _session.html) rendered on the demo
person's data, so they're today's and never a stale screenshot. Building that context is the
expensive part of a page, and the demo only changes once a day, so it is kept for ten
minutes: a link shared on LinkedIn shouldn't rebuild every chart for every visitor.
"""
import time
from datetime import date

from estecoach.web import people, signups

CACHE_S = 600
_cache = {}


def demo_context(request, context_fn):
    key = date.today().isoformat()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    p = people.demo()
    ctx = context_fn(request, p) if p else None
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
        ask_first=False, ask=ask, tracks=signups.TRACKS, form=form or {}, error=error, done=done,
        limits=signups.LIMITS), status_code=status)

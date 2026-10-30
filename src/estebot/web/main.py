"""FastAPI app. Thin layer over analysis/engine.py - it formats, it does not compute."""
import os, re, statistics as st, subprocess, sys
from collections import Counter
import logging
from datetime import date, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from estebot import config, core
from estebot.analysis import calls, engine as an, nutrition, predictions
import anthropic
from starlette.concurrency import run_in_threadpool

from estebot.web import auth, chat, checkin_text, db, charts, examples, people

log = logging.getLogger("estebot")
HERE = os.path.dirname(__file__)
app = FastAPI(title="health")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
# The stylesheet URL carries a hash of its content, so a browser never pairs new markup with
# a cached old stylesheet (15 Oct: unstyled reference labels and one-column charts).
import hashlib
def _static_v(name):
    with open(os.path.join(HERE, "static", name), "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:10]
tpl.env.globals["css_v"] = _static_v("app.css")
tpl.env.globals["icon_v"] = _static_v("icon.svg")       # same reason: a cached old icon
tpl.env.filters["shortdate"] = lambda iso: date.fromisoformat(iso).strftime("%a %-d %b")

# The public demo (ESTEBOT_DEMO=1) is one page shared by every visitor, so nobody gets to
# change it: the forms are not rendered and the POST routes refuse.
def demo_mode():
    return os.environ.get("ESTEBOT_DEMO") == "1"

def _refuse(p, csrf):
    """None if this person may change their page, else the response refusing it. The demo is
    read-only; a signed-in person's forms must carry their session's CSRF token."""
    if demo_mode() or p.read_only:
        return PlainTextResponse("This is a read-only demo.", status_code=403)
    s = auth.settings()
    if s is not None and not auth.csrf_ok(s, p.session, csrf):
        return PlainTextResponse("This form has expired. Reload the page and try again.",
                                 status_code=403)

# The engine's verdicts, in words. "hard" needs an earned reason to go for every target.
VERDICT_TEXT = {"hard": "Go hard", "normal": "Go as planned", "light": "Take it easy", "rest": "Rest day"}
BACK_LEVELS = ("ok", "tired", "sore", "spasm")      # checkin.back, mildest first

@app.get("/", response_class=HTMLResponse)
def today(request: Request, day: str | None = None, present: str | None = None):
    p = people.for_request(request)
    cfg, goals = p.cfg, p.goals         # goals are starting values; the page can set its own goal
    con = p.connect()
    day = day or an.default_day(con, date.today().isoformat())
    r = an.readiness(con, day, goals)

    ledger = cfg.data_file("ledger.md")
    preds = predictions.panel(con, an, ledger.read_text() if ledger.exists() else "",
                              predictions.load_specs(cfg.data_file("predictions.toml")), day)

    # weight: raw points + 14-day rolling trend
    since = (date.fromisoformat(day) - timedelta(days=120)).isoformat()
    w = an.weight_series(con, since, day)
    roll = an.rolling(w, 14)
    wchart = charts.line_chart(
        [{"name": "14-day trend", "points": roll, "slot": 1},
         {"name": "morning weigh-in", "points": w, "slot": 2, "dashed": True}],
        ylabel="body weight (lb)", fmt="{:.0f}")
    goal = float(db.get_setting(con, "goal_weight_lb", goals.get("target_weight_lb", 0)) or 0)
    ws = an.weight_status(con, day, goal, goals.get("max_loss_pct_per_week"))
    pace_range = (goals.get("min_loss_pct_per_week", 0.5), goals.get("max_loss_pct_per_week", 1.0))
    wc = an.weight_changes(con, day, *pace_range)
    strength = an.strength_standards(con, day, ws["current"], goal or None)

    # training load, last 8 weeks
    lstart = (date.fromisoformat(day) - timedelta(days=55)).isoformat()
    # Training load: three measures, three charts on one shared day-by-day axis.
    tl = an.training_load_series(con, lstart, day)
    bars = dict(height=130)
    load_charts = [
        ("Exercise minutes", charts.bar_chart(tl["minutes"], **bars)),
        ("Hard sets",
         charts.bar_chart(tl["hard_sets"], **bars)),
        ("Tonnage, thousand lb",
         charts.bar_chart(tl["tonnage_klb"], fmt="{:.0f}", **bars)),
    ]

    # The readiness inputs, one small chart each (one measure per axis), with the
    # thresholds the verdict actually applies drawn on them.
    rs = an.recovery_series(con, lstart, day)
    small = dict(width=360, height=150, pad_l=40, dot=2.5)
    multiples = [
        ("Overnight HRV (ms)", charts.line_chart(
            [{"name": "HRV", "points": rs["hrv"], "slot": 1}], **small,
            refs=[(r["hrv_baseline"], "usual (30 days before this week)")] if r["hrv_baseline"] else [])),
        ("Resting heart rate (bpm)", charts.line_chart(
            [{"name": "Resting HR", "points": rs["rhr"], "slot": 1}], **small,
            refs=[(r["rhr_baseline"] + 3, "baseline + 3")] if r["rhr_baseline"] else [])),
        ("Sleep (hours asleep)", charts.line_chart(
            [{"name": "Sleep", "points": rs["sleep"], "slot": 1}], **small, fmt="{:.1f}",
            refs=[(5, "under 5 h: strain")])),
        ("Load ratio (7-day vs 28-day)", charts.line_chart(
            [{"name": "Load ratio", "points": rs["acwr"], "slot": 1}], **small, fmt="{:.1f}",
            refs=[(0.8, "0.8 light week"), (1.5, "1.5 injury risk")])),
    ]

    # word-sized trends for the tiles, from the same series as the full charts
    sparks = {"weight": charts.sparkline(roll), "hrv": charts.sparkline(rs["hrv"]),
              "load": charts.sparkline(rs["acwr"])}

    pattern = an.recommend_pattern(con, day, r["checkin"], training=p.training)
    plan = an.training_plan(con, day, r["verdict"], r["checkin"], training=p.training)
    axial = an.axial_by_day_type(con, day, training=p.training)
    checkin = r["checkin"]               # the row the verdict used: today's, else yesterday's
    # The tap card prefills from this day's own row, never yesterday's: carrying yesterday's
    # answers forward would save them as today's with one tap.
    day_ci = con.execute("SELECT * FROM checkin WHERE date=?", (day,)).fetchone()
    # A check-in said in a sentence prefills the card until it's saved; a manual save after
    # it clears it, so a draft never sits over what was confirmed.
    draft = con.execute("SELECT * FROM checkin_draft WHERE date=?", (day,)).fetchone()
    if draft:
        day_ci = dict(draft, source="draft", back_reported=int(draft["back"] is not None),
                      back=draft["back"] or "ok", soreness=(day_ci["soreness"] if day_ci else 0))

    # days that still need a subjective answer, so they can be reached from the UI. Only the
    # last week's: an answer only changes that day's call (and the next, through "how you
    # feel"), so an old flag stays in the table but stops being asked about.
    dd = date.fromisoformat(day)
    review_days = [r["date"] for r in con.execute(
        "SELECT date FROM checkin WHERE needs_review=1 AND date BETWEEN ? AND ? "
        "ORDER BY date DESC LIMIT 5", ((dd - timedelta(days=7)).isoformat(), day))]
    prev_day, next_day = (dd - timedelta(days=1)).isoformat(), (dd + timedelta(days=1)).isoformat()

    calendar = _calendar(con, day)
    notes = [dict(r) for r in con.execute(
        "SELECT * FROM annotation WHERE start_date >= ? ORDER BY start_date DESC LIMIT 6",
        ((date.fromisoformat(day) - timedelta(days=45)).isoformat(),))]

    # Nutrition: complete days only (nutrition.py), deficits from two labelled sources.
    nut = {"energy": nutrition.energy_balance(con, day, ws["rate"]),
           "protein": nutrition.protein(con, day, ws["current"]),
           "protein_target_g": nutrition.protein_target(goals, ws["current"]),
           "protein_per_lb": goals.get("protein_g_per_lb_target")}
    products, supp_notes = nutrition.load_supplements(cfg.data_file("supplements.toml"))
    nut["supplements"] = [p["name"] for p in products if nutrition.active(p, day)]
    nut["gaps"], nut["near_limit"], nut["unmeasured"] = nutrition.micronutrients(
        con, day, nutrition.supplement_amounts(products, day), supp_notes)
    verdict_text = VERDICT_TEXT[r["verdict"]]
    # The evidence behind the votes (cached by the engine) and the self-grade on recorded calls.
    ev = an.earned(con, day, goals)["evidence"]
    recorded = calls.graded(con, calls.load(cfg.data_file("calls.csv")))
    ctx = dict(verdict_text=verdict_text, verdict_words=VERDICT_TEXT, ev=ev, recorded=recorded,
        ring=charts.signal_ring(r["signals"], r["score"], r["verdict"], voting=r["voting"]),
        day_label=date.fromisoformat(day).strftime("%a %-d %b %Y"),
        day=day, r=r, preds=preds, wchart=wchart, load_charts=load_charts,
        multiples=multiples, sparks=sparks, ws=ws, wc=wc, strength=strength, pace_range=pace_range, nut=nut, calendar=calendar, goals_max=goals.get('max_loss_pct_per_week', 1.0), latest_w=(w[-1][1] if w else None), notes=notes,
        pattern=pattern, plan=plan, axial=axial, checkin=checkin,
        day_ci=dict(day_ci) if day_ci else None, back_levels=BACK_LEVELS,
        said=request.query_params.get("said"),
        review_days=review_days, prev_day=prev_day, next_day=next_day,
        is_today=(day == date.today().isoformat()), demo=demo_mode() or p.read_only,
        signin=auth.settings() is not None, email=p.email, chat_on=chat_enabled(p),
        chat_history=(chat.recent(con) if chat_enabled(p) else
                      examples.conversation(con, day, goals, r, ws, strength) if demo_mode() else []),
        csrf=auth.csrf_token(auth.settings(), p.session) if p.session else "")
    on = request.cookies.get(PRESENT_COOKIE) == "1" if present is None else present == "1"
    resp = tpl.TemplateResponse(request, "today.html", presentable(ctx) if on else dict(ctx, present=False))
    if present is not None:
        resp.set_cookie(PRESENT_COOKIE, "1" if on else "0", max_age=365 * 86400,
                        samesite="lax", httponly=True)
    return resp


# Presentation mode: the page as it can be shown to someone else. It keeps the training,
# weight and strength story and leaves out what is medical or intimate. The filtering is
# done here, before the template, so the hidden text never reaches the HTML; a CSS-hidden
# note would still be one view-source away. A cookie keeps it on across ?day= links and
# the redirects after a form post.
PRESENT_COOKIE = "present"
def presentable(ctx):
    """A copy of the page context with the private parts taken out. Drops: every note's text
    (check-in, recent context, calendar tooltips), the sick/injured calendar rings, back
    status and the sick/injured flags wherever they show, the supplement list and
    per-nutrient notes, the upper-limit table (it is about supplement doses) and predictions
    marked private. The template also drops the forms on `present`, and the check-in card's
    prefill (`day_ci`) goes here, so its note and toggles never reach the HTML.
    Numbers are untouched."""
    # The chat is off too, and its history left out: an answer could quote anything in the data.
    c = dict(ctx, present=True, notes=[], review_days=[], pattern=None, day_ci=None, chat_on=False,
             chat_history=[])
    r = c["r"] = dict(ctx["r"], subjective_review=False)
    # Back status is an injury detail: its reasons go and the rest of the signal stays.
    r["signals"] = [dict(x, reasons=[t for t in x["reasons"] if "back" not in t] or
                         ["from the daily check-in"]) if x["area"] == "feel" else x
                    for x in r["signals"]]
    # The hold stays (it decided the day), its reason doesn't: sick and injured are medical.
    r["holds"] = [h if h.startswith("back from") else "a health note" for h in ctx["r"].get("holds", [])]
    if r.get("positive", "") and "checked in" in r["positive"]:
        r["positive"] = "a good check-in this morning"
    if ctx["checkin"]:
        c["checkin"] = r["checkin"] = dict(ctx["checkin"], note=None, back_reported=0, needs_review=0,
                                           sick=0, injured=0)
    if ctx["plan"]:
        c["plan"] = dict(ctx["plan"], exercises=[
            dict(i, adjust=re.sub(r"^Back \w+: (\w)", lambda m: m[1].upper(), i["adjust"]))
            if i.get("adjust") else i for i in ctx["plan"]["exercises"]])
    c["calendar"] = [dict(m, weeks=[[x and dict(x, unwell=None) for x in w] for w in m["weeks"]])
                     for m in ctx["calendar"]]
    c["nut"] = dict(ctx["nut"], supplements=[], near_limit=[],
                    gaps=[dict(g, note=None) for g in ctx["nut"]["gaps"]])
    # predictions.toml marks the private ones; guessing from their wording would need a
    # list of exactly the words that should stay out of a public repo.
    c["preds"] = [p for p in ctx["preds"] if not p.get("private")]
    return c


def _activity_name(a):
    """Apple's 'TraditionalStrengthTraining' -> 'Traditional strength training'."""
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", a or "").lower()
    return words[:1].upper() + words[1:]


def _calendar(con, day, months=3):
    """Month grids (Mon-Sun) for the `months` months ending with the one holding `day`.
    Only formats: the training/rest call is engine.training_days."""
    end = date.fromisoformat(day)
    first = date(end.year, end.month, 1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    info = an.training_days(con, first.isoformat(), end.isoformat())
    unwell = an.unwell_days(con, first.isoformat(), end.isoformat())
    out, m = [], first
    while m <= end:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        cells = [None] * m.weekday()
        x = m
        while x < nxt:
            k = x.isoformat()
            i = info.get(k)
            cells.append({"date": k, "n": x.day, "future": i is None, "today": k == day,
                          "training": bool(i and i["training"]), "kind": (i or {}).get("kind"), "unwell": unwell.get(k),
                          "info": i,
                          "workouts": [(_activity_name(a), dm, an.is_light_movement(a))
                                       for a, dm in (i or {}).get("workouts", [])]})
            x += timedelta(days=1)
        cells += [None] * (-len(cells) % 7)
        out.append({"title": m.strftime("%B %Y"), "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)]})
        m = nxt
    return out


@app.post("/goal")
def set_goal(request: Request, goal_weight_lb: float = Form(...), day: str = Form(""),
             csrf: str = Form("")):
    p = people.for_request(request)
    if refused := _refuse(p, csrf):
        return refused
    con = p.connect()
    db.set_setting(con, "goal_weight_lb", round(goal_weight_lb, 1))
    return RedirectResponse(f"/?day={day}" if day else "/", status_code=303)


def _record_call(p):
    """Record today's call now, in the background, so a morning check-in is in it."""
    sync_log = open(p.cfg.data_dir / ".sync.log", "a")
    subprocess.Popen([sys.executable, "-m", "estebot.analysis.calls"], stdout=sync_log, stderr=sync_log,
                     env={**os.environ, "ESTEBOT_CONFIG": str(p.cfg.path)}, start_new_session=True)

@app.post("/checkin")
def checkin(request: Request, day: str = Form(...), back: str | None = Form(None),
            soreness: int = Form(0), energy: int | None = Form(None),
            inter_set_recovery: int | None = Form(None), note: str = Form(""),
            sick: bool = Form(False), injured: bool = Form(False), csrf: str = Form("")):
    p = people.for_request(request)
    if refused := _refuse(p, csrf):
        return refused
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        return PlainTextResponse("Bad date.", status_code=400)
    # Every field is a tap, and an untapped one is left unanswered rather than given a
    # default: a back nobody reported must not read as a green "back ok".
    reported = back in BACK_LEVELS
    scale = lambda v: v if v in (0, 1, 2, 3) else None
    energy = core.checkin_energy(scale(energy), sick)
    con = p.connect()
    # A manual save always wins over a derived row and clears the review flag - answering
    # the question is what resolving it means.
    con.execute("""INSERT INTO checkin
                     (date, back, back_reported, soreness, energy, inter_set_recovery, note,
                      sick, injured, source, needs_review)
                   VALUES (?,?,?,?,?,?,?,?,?,'manual',0)
                   ON CONFLICT(date) DO UPDATE SET
                     back=excluded.back, back_reported=excluded.back_reported,
                     soreness=excluded.soreness, energy=excluded.energy,
                     inter_set_recovery=excluded.inter_set_recovery,
                     note=excluded.note, sick=excluded.sick, injured=excluded.injured,
                     source='manual', needs_review=0""",
                (day, back if reported else "ok", int(reported), soreness, energy,
                 scale(inter_set_recovery), note.strip()[:400] or None, int(sick), int(injured)))
    con.execute("DELETE FROM checkin_draft WHERE date=?", (day,))
    con.commit()
    if day == date.today().isoformat():
        _record_call(p)
    return RedirectResponse(f"/?day={day}", status_code=303)


@app.post("/checkin/text")
def checkin_from_text(request: Request, day: str = Form(...), text: str = Form(""), csrf: str = Form("")):
    """Read a sentence onto the tap card as a draft. The sentence is kept as the note."""
    p = people.for_request(request)
    if refused := _refuse(p, csrf):
        return refused
    if not chat_enabled(p):
        return PlainTextResponse("Reading a check-in isn't available here.", status_code=403)
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        return PlainTextResponse("Bad date.", status_code=400)
    con = p.connect()
    try:
        f = checkin_text.read(con, text, date.today().isoformat())
    except checkin_text.CheckinTextError as e:
        log.warning("check-in not read: %s", e)
        return RedirectResponse(f"/?day={day}&said=failed#checkin", status_code=303)
    con.execute("""INSERT OR REPLACE INTO checkin_draft
                     (date, back, energy, inter_set_recovery, sick, injured, note)
                   VALUES (?,?,?,?,?,?,?)""",
                (day, f["back"], f["energy"], f["inter_set_recovery"], int(f["sick"]),
                 int(f["injured"]), text.strip()[:checkin_text.MAX_CHARS]))
    con.commit()
    return RedirectResponse(f"/?day={day}#checkin", status_code=303)


# ---------------------------------------------------------------- sign-in (web/auth.py)

def _set(resp, name, value, seconds, s):
    resp.set_cookie(name, value, max_age=seconds, httponly=True, samesite="lax", secure=s.secure)

@app.get("/auth/login")
def login():
    s = auth.settings()
    if s is None:
        return RedirectResponse("/", status_code=303)
    url, flow = auth.start(s)
    resp = RedirectResponse(url, status_code=303)
    _set(resp, auth.FLOW_COOKIE, flow, auth.FLOW_SECONDS, s)
    return resp

@app.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    s = auth.settings()
    if s is None:
        return RedirectResponse("/", status_code=303)
    if error or not code:
        return PlainTextResponse("Sign-in was cancelled.", status_code=400)
    try:
        email = auth.finish(s, request.cookies.get(auth.FLOW_COOKIE), state, code)
    except auth.SignInError as e:
        return PlainTextResponse(str(e), status_code=400)
    if email not in s.users:
        # Same answer for every unknown address: the page doesn't say who has an account.
        return PlainTextResponse("This Google account doesn't have an EsteBot page.", status_code=403)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.FLOW_COOKIE)
    _set(resp, auth.SESSION_COOKIE, auth.session_cookie(s, email), auth.SESSION_DAYS * 86400, s)
    return resp

@app.post("/auth/logout")
def logout(request: Request, csrf: str = Form("")):
    s = auth.settings()
    resp = RedirectResponse("/", status_code=303)
    if s is not None and auth.csrf_ok(s, auth.session(request, s), csrf):
        resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------- phone uploads

MAX_UPLOAD = 100 * 1024 * 1024       # a day of minute-level metrics is about 30 MB
_DAY = re.compile(rb'"(?:date|start)"\s*:\s*"(\d{4}-\d{2}-\d{2})')

def _feed_day(body):
    """The day an upload is for, from its own timestamps ("2026-10-28 00:04:06 -0400"), so
    the file is named for the phone's day and not the server's UTC one. A day's export can
    also hold the previous night's sleep, so it's the most common day, not the first."""
    days = Counter(m.group(1).decode() for m in _DAY.finditer(body))
    return days.most_common(1)[0][0] if days else date.today().isoformat()

@app.post("/api/health-auto-export")
async def health_auto_export(request: Request):
    """Health Auto Export's REST automation posts here instead of writing to iCloud Drive.
    The body is saved byte for byte where the feed parser already looks (the folder of the
    config's metrics_glob, or workouts_glob for a workouts export), one file per day like
    the iCloud files, and the feed part of the sync runs in the background. It isn't parsed
    here: a day's file is 30 MB of JSON and 170 MB once parsed, and the sync parses it anyway."""
    s = auth.settings()
    hdr = request.headers.get("authorization") or ""
    def refuse(status, msg, size=None):
        # The phone app shows only "export did not complete", so the reason goes in the log:
        # the shape of the request, never the token and no health values.
        log.warning("upload refused %s: %s (auth=%s, type=%s, bytes=%s)", status, msg,
                    hdr.split(" ")[0] or "none", request.headers.get("content-type"), size)
        return PlainTextResponse(msg, status_code=status)
    path = auth.upload_config(s, hdr) if s else None
    if path is None:
        return refuse(401, "Unknown upload token.")
    body = await request.body()
    if len(body) > MAX_UPLOAD:
        return refuse(413, "Upload too large.", len(body))
    head = re.sub(rb"\s", b"", body[:400])
    kind = "workouts" if b'"workouts"' in head else "metrics" if b'"metrics"' in head else None
    if not head.startswith(b'{"data":') or kind is None:
        log.warning("upload starts %r", re.sub(rb'"\d[^"]*"|\d+(\.\d+)?', b"#", head[:80]))
        return refuse(400, "Expected Health Auto Export JSON (Export Version 2).", len(body))
    p = people.load(path)
    glob_key = f"{kind}_glob"
    pattern = p.cfg.sources.get("health_auto_export", {}).get(glob_key)
    if not pattern:
        return PlainTextResponse(f"This config has no {glob_key}.", status_code=422)
    folder = Path(os.path.expanduser(pattern)).parent
    if not folder.is_relative_to(p.cfg.data_dir):
        return PlainTextResponse(f"{glob_key} must be inside the data directory.", status_code=500)
    folder.mkdir(parents=True, exist_ok=True)
    day = _feed_day(body)
    tmp = folder / f".upload-{day}.tmp"
    tmp.write_bytes(body)
    tmp.replace(folder / f"HealthAutoExport-{day}.json")          # atomic: never half a file
    sync_log = open(p.cfg.data_dir / ".sync.log", "a")
    subprocess.Popen([sys.executable, "-m", "estebot.daily", "--feed"], stdout=sync_log, stderr=sync_log,
                     env={**os.environ, "ESTEBOT_CONFIG": str(p.cfg.path)}, start_new_session=True)
    log.warning("upload saved: %s for %s, %d bytes", kind, day, len(body))
    return PlainTextResponse(f"Saved {kind} for {day}. Refreshing.", status_code=202)


# ---------------------------------------------------------------- Ask EsteBot (web/chat.py)

def chat_enabled(p):
    """On for a person's own page when there's an API key. Never for the demo: anyone could
    run up the bill, and there's nothing of theirs to ask about."""
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and not (demo_mode() or p.read_only)

def _chat_refusal(request, p):
    if not chat_enabled(p):
        return JSONResponse({"error": "Chat isn't available here."}, status_code=403)
    s = auth.settings()
    if s is not None and not auth.csrf_ok(s, p.session, request.headers.get("x-csrf-token")):
        return JSONResponse({"error": "This page has expired. Reload it and try again."}, status_code=403)

@app.post("/api/chat")
async def ask(request: Request):
    p = people.for_request(request)
    if (refused := _chat_refusal(request, p)) is not None:
        return refused
    try:
        history = (await request.json()).get("history")
    except ValueError:
        history = None
    if not chat.history_ok(history):
        return JSONResponse({"error": "That message couldn't be read."}, status_code=400)
    def run():                       # in a worker thread, so the connection is opened there too
        con, today = p.connect(), date.today().isoformat()
        answer, spent = chat.ask(p, con, history, today)
        chat.save(con, history[-1]["text"], answer, today)
        return answer, spent
    try:
        answer, spent = await run_in_threadpool(run)
    except chat.ChatError as e:
        return JSONResponse({"error": str(e)}, status_code=429)
    except anthropic.APIError as e:
        log.warning("chat failed: %s", type(e).__name__)
        return JSONResponse({"error": "Claude didn't answer. Try again in a minute."}, status_code=502)
    return JSONResponse({"answer": answer, "cost_usd": round(spent, 4)})

@app.post("/api/chat/clear")
async def clear_chat(request: Request):
    p = people.for_request(request)
    if (refused := _chat_refusal(request, p)) is not None:
        return refused
    await run_in_threadpool(lambda: chat.clear(p.connect()))
    return JSONResponse({"cleared": True})

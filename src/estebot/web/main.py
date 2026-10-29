"""FastAPI app. Thin layer over analysis/engine.py - it formats, it does not compute."""
import os, re, statistics as st
from datetime import date, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from estebot import config, core
from estebot.analysis import engine as an, nutrition, predictions
from estebot.web import auth, db, charts, people

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

# The engine's verdicts, in words. "hard" means everything lines up for a big session.
VERDICT_TEXT = {"hard": "Go all out", "normal": "Normal", "light": "Take it easy", "rest": "Rest day"}
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
        ("Exercise minutes (Apple, all activity)", charts.bar_chart(tl["minutes"], **bars)),
        ("Hard sets (RPE 7+, or every working set where no RPE was logged)",
         charts.bar_chart(tl["hard_sets"], **bars)),
        ("Tonnage, thousand lb (logged weight × reps; pull-ups add sets, not pounds)",
         charts.bar_chart(tl["tonnage_klb"], fmt="{:.0f}", **bars)),
    ]

    # The readiness inputs, one small chart each (one measure per axis), with the
    # thresholds the verdict actually applies drawn on them.
    rs = an.recovery_series(con, lstart, day)
    small = dict(width=360, height=150, pad_l=40, dot=2.5)
    multiples = [
        ("Overnight HRV (ms)", charts.line_chart(
            [{"name": "HRV", "points": rs["hrv"], "slot": 1}], **small,
            refs=[(r["hrv_baseline"], "30-day baseline")] if r["hrv_baseline"] else [])),
        ("Resting heart rate (bpm)", charts.line_chart(
            [{"name": "Resting HR", "points": rs["rhr"], "slot": 1}], **small,
            refs=[(r["rhr_baseline"] + 3, "baseline + 3")] if r["rhr_baseline"] else [])),
        ("Sleep (hours asleep)", charts.line_chart(
            [{"name": "Sleep", "points": rs["sleep"], "slot": 1}], **small, fmt="{:.1f}",
            refs=[(5, "under 5 h: take it easy")])),
        ("Load ratio (7-day vs 28-day)", charts.line_chart(
            [{"name": "Load ratio", "points": rs["acwr"], "slot": 1}], **small, fmt="{:.1f}",
            refs=[(0.8, "0.8"), (1.5, "1.5 injury risk")])),
    ]

    pattern = an.recommend_pattern(con, day, r["checkin"], training=p.training)
    plan = an.training_plan(con, day, r["verdict"], r["checkin"], training=p.training)
    axial = an.axial_by_day_type(con, day, training=p.training)
    checkin = r["checkin"]               # the row the verdict used: today's, else yesterday's
    # The tap card prefills from this day's own row, never yesterday's: carrying yesterday's
    # answers forward would save them as today's with one tap.
    day_ci = con.execute("SELECT * FROM checkin WHERE date=?", (day,)).fetchone()

    # days that still need a subjective answer, so they can be reached from the UI
    review_days = [r["date"] for r in con.execute(
        "SELECT date FROM checkin WHERE needs_review=1 ORDER BY date DESC LIMIT 5")]
    dd = date.fromisoformat(day)
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
    ctx = dict(verdict_text=verdict_text,
        day_label=date.fromisoformat(day).strftime("%a %-d %b %Y"),
        day=day, r=r, preds=preds, wchart=wchart, load_charts=load_charts,
        multiples=multiples, ws=ws, wc=wc, strength=strength, pace_range=pace_range, nut=nut, calendar=calendar, goals_max=goals.get('max_loss_pct_per_week', 1.0), latest_w=(w[-1][1] if w else None), notes=notes,
        pattern=pattern, plan=plan, axial=axial, checkin=checkin,
        day_ci=dict(day_ci) if day_ci else None, back_levels=BACK_LEVELS,
        review_days=review_days, prev_day=prev_day, next_day=next_day,
        is_today=(day == date.today().isoformat()), demo=demo_mode() or p.read_only,
        signin=auth.settings() is not None, email=p.email,
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
    c = dict(ctx, present=True, notes=[], review_days=[], pattern=None, day_ci=None)
    r = c["r"] = dict(ctx["r"], subjective_review=False)
    # Back status is an injury detail: its reasons go and the rest of the signal stays.
    r["signals"] = [dict(x, reasons=[t for t in x["reasons"] if "back" not in t] or
                         ["from the daily check-in"]) if x["area"] == "feel" else x
                    for x in r["signals"]]
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
    con.commit()
    return RedirectResponse(f"/?day={day}", status_code=303)


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

"""Example questions and answers for the demo's Ask EsteBot panel.

The chat is off in the demo, so the panel shows what it can do instead. The demo person
is generated again every day, so fixed answers would soon contradict the page beside
them: the wording here is fixed, and every number is filled in from the same engine
results the page shows. Nothing is calculated here; numbers are only formatted.
"""
from datetime import date, timedelta

from estebot.analysis import engine as an, nutrition, predictions
from estebot.web import charts

AREA = {"load": "training load", "effort": "effort", "sleep": "sleep", "recovery": "HRV and resting heart rate",
        "feel": "how you feel", "nutrition": "nutrition", "pace": "weight-loss pace"}
VERDICT = {"hard": "Go hard", "normal": "Go as planned", "light": "Take it easy", "rest": "Rest"}


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _call(r):
    voting = r.get("voting", [])
    flagged = [x for x in r["signals"] if x["level"] in ("red", "amber")]
    counted = [x for x in flagged if x["area"] in voting]
    a = f"{VERDICT[r['verdict']]}, readiness {r['score']}."
    if r.get("holds"):
        a += " Held by a safety rule: " + _join(r["holds"]) + "."
    if counted:
        a += " What's holding you back:\n" + "\n".join(
            f"- {AREA[x['area']].capitalize()}: {x['reasons'][0]}" for x in counted if x["reasons"])
    if r.get("positive"):
        a += f" The reason to go hard: {r['positive']}, and days like that have gone better for you."
    if voting:
        a += ("\n\nOnly " + _join([AREA[v] for v in voting]) + (" has" if len(voting) == 1 else " have")
              + " earned a vote: days it was flagged, your sessions went worse. The other signals are"
              " shown but don't decide the day.")
    else:
        a += ("\n\nNo signal has earned a vote yet: none has gone with worse sessions for you, so the"
              " day stays as planned until one does.")
    other = [x for x in flagged if x["area"] not in voting]
    if other:
        a += " Flagged but not counted: " + _join([AREA[x["area"]] for x in other]) + "."
    return {"q": "What's behind today's call?", "a": a}


def _squat(con, day, goals, st, specs):
    """The squat now against the start of the cut: the same baseline window and the same
    statistic (best e1RM in it) as the strength prediction, which the example once didn't
    share, so it said +15% from the first session beside P-002's +8% from the first month."""
    now = next((x for x in st["rows"] if x["label"] == "Squat" and x.get("e1rm")), None)
    spec = next((p for p in specs.values() if p.get("metric") == "e1rm_median_change"
                 and "Barbell Squat" in p.get("lifts", [])), None)
    if spec:
        base = tuple(spec["baseline"])
    else:
        first = con.execute("SELECT MIN(date) FROM e1rm WHERE exercise = 'Barbell Squat'").fetchone()[0]
        base = first and (first, (date.fromisoformat(first) + timedelta(days=27)).isoformat())
    then = base and an.e1rm_window_best(con, *base, ["Barbell Squat"]).get("Barbell Squat")
    if not then or not now:
        return None
    then_w = an.weight_status(con, base[1], float(goals.get("target_weight_lb") or 0))["current"]
    up = now["e1rm"] > then
    span = f"{date.fromisoformat(base[0]):%-d %b} to {date.fromisoformat(base[1]):%-d %b %Y}"
    a = (("Yes. " if up else "Not yet. ") + f"Your best estimated squat in the first four weeks of the cut "
         f"({span}) was {then:.0f} lb" + (f", at a body weight of {then_w:.0f} lb" if then_w else "")
         + f". Your best over the last {st['weeks']} weeks is {now['e1rm']:.0f} lb at {st['body_lb']:.0f} lb, "
         f"{(now['e1rm'] - then) / then * 100:+.1f}%: {now['ratio']:.2f}× body weight, which rates {now['level']}.")
    if spec:
        p = predictions.e1rm_median_change(con, an, spec, day)
        pct = p["per"].get("Barbell Squat")
        mine = f"{(now['e1rm'] - then) / then * 100:+.1f}"
        if pct is not None and f"{pct:+.1f}" == mine:
            a += " The predictions table shows the same figure for squat (P-002)."
        elif pct is not None:
            a += (f" P-002 has squat at {pct:+.1f}% from the same start because it takes your best at any "
                  f"point since {date.fromisoformat(p['window'][0]):%-d %b %Y}; this takes the last {st['weeks']} weeks.")
    a += ("\n\nStrength going up while body weight comes down is the cut working: you're holding on "
          "to muscle while the fat comes off." if up else
          "\n\nOn a cut, holding strength is a win; check that protein is on target and the "
          "deficit isn't too steep.")
    if st.get("dots"):
        a += f" Squat, bench and deadlift together come to {st['total']:,.0f} lb, a DOTS of {st['dots']:.0f}."
    return {"q": "Is my squat actually getting stronger while I'm cutting?", "a": a}


def _scale(con, day, goals, ws):
    """Asked the way this week reads on the weight card (weight_changes' trend), so the
    question's premise is true on the day shown: "why did the scale barely move" sat beside
    a week that lost more than the 4-week pace."""
    wc = an.weight_changes(con, day, goals.get("min_loss_pct_per_week", 0.5), goals.get("max_loss_pct_per_week", 1.0))
    wow, mom = wc["wow"], wc["mom"]
    if wow.get("change_lb") is None or mom.get("change_lb") is None:
        return None
    if wow["change_lb"] > 0:
        q, why = "Why did the scale go up this week?", "so a week up inside a longer trend says little"
    elif wc["trend"] == "slowing":
        q = ("Why did the scale barely move this week?" if wow["pct"] < 0.15 else
             "Why did I lose less this week than lately?")
        why = "so a slow week inside a steady month is noise"
    elif wc["trend"] == "speeding_up":
        q, why = ("Why did I lose more this week than lately?",
                  "so a fast week is mostly water too, not a change of pace")
    else:
        q, why = "Am I losing weight at the right pace?", "so the week only matters because it agrees with the month"
    month = {"on_pace": ", right on your planned pace.", "slow": ", slower than your planned pace.",
             "fast": ", faster than your planned pace.", "gaining": ", a gain over the month."}
    a = (f"The last 7 days average {wow['change_lb']:+.1f} lb against the 7 before, "
         + ("and" if wc["trend"] is None and wow["change_lb"] <= 0 else "but")
         + f" the last 4 weeks are {mom['change_lb']:+.1f} lb against the 4 before, "
         f"{mom['pct']:.2f}% of body weight a week" + month.get(mom["status"], "."))
    a += f"\n\nA single week swings 2–3 lb on water and glycogen alone, {why}. The 4-week line is the one to act on."
    e = nutrition.energy_balance(con, day, ws["rate"])
    pr, target = nutrition.protein(con, day, ws["current"]), nutrition.protein_target(goals, ws["current"])
    if e.get("measured") is not None:
        a += f"\n\nOver the last {e['n']} complete food days, the scale says you're running a {charts.kcal_balance(e['measured'])} a day"
        a += (f"; your food and activity logs say a {charts.kcal_balance(e['logged'])}. Over weeks, trust the scale."
              if e.get("logged") is not None else ".")
    if pr.get("mean") and target:
        a += f" Protein is {pr['mean']:.0f} g a day against a {target:.0f} g target, which protects muscle on a cut."
    return {"q": q, "a": a}


def conversation(con, day, goals, r, ws, st, specs=None):
    """[{role, text}] in the chat panel's format, built from today's engine results.
    specs: predictions.toml, for the squat example's baseline."""
    pairs = [_call(r), _squat(con, day, goals, st, specs or {}), _scale(con, day, goals, ws)]
    return [m for p in pairs if p for m in ({"role": "user", "text": p["q"]}, {"role": "assistant", "text": p["a"]})]

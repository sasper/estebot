"""Example questions and answers for the demo's Ask EsteBot panel.

The chat is off in the demo, so the panel shows what it can do instead. The demo person
is generated again every day, so fixed answers would soon contradict the page beside
them: the wording here is fixed, and every number is filled in from the same engine
results the page shows. Nothing is calculated here; numbers are only formatted.
"""
from datetime import date

from estebot.analysis import engine as an, nutrition

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


def _squat(con, day, goals, st):
    first = con.execute("SELECT date, best_e1rm_lb FROM e1rm WHERE exercise = 'Barbell Squat' "
                        "ORDER BY date LIMIT 1").fetchone()
    now = next((x for x in st["rows"] if x["label"] == "Squat" and x.get("e1rm")), None)
    if not first or not now:
        return None
    then_w = an.weight_status(con, first["date"], float(goals.get("target_weight_lb") or 0))["current"]
    when = date.fromisoformat(first["date"]).strftime("%B")
    up = now["e1rm"] > first["best_e1rm_lb"]
    a = (("Yes. " if up else "Not yet. ") + f"Your best estimated squat was {first['best_e1rm_lb']:.0f} lb in {when}"
         + (f", at a body weight of {then_w:.0f} lb" if then_w else "")
         + f". Over the last {st['weeks']} weeks it's {now['e1rm']:.0f} lb at {st['body_lb']:.0f} lb, "
         f"{now['ratio']:.2f}× body weight, which rates {now['level']}.")
    a += ("\n\nStrength going up while body weight comes down is the cut working: you're holding on "
          "to muscle while the fat comes off." if up else
          "\n\nOn a cut, holding strength is a win; check that protein is on target and the "
          "deficit isn't too steep.")
    if st.get("dots"):
        a += f" Squat, bench and deadlift together come to {st['total']:,.0f} lb, a DOTS of {st['dots']:.0f}."
    return {"q": "Is my squat actually getting stronger while I'm cutting?", "a": a}


def _scale(con, day, goals, ws):
    wc = an.weight_changes(con, day, goals.get("min_loss_pct_per_week", 0.5), goals.get("max_loss_pct_per_week", 1.0))
    wow, mom = wc["wow"], wc["mom"]
    if wow.get("change_lb") is None or mom.get("change_lb") is None:
        return None
    e = nutrition.energy_balance(con, day, ws["rate"])
    pr, target = nutrition.protein(con, day, ws["current"]), nutrition.protein_target(goals, ws["current"])
    a = (f"This week's average is {wow['change_lb']:+.1f} lb against last week's, but the last 4 weeks are "
         f"{mom['change_lb']:+.1f} lb against the 4 before, {mom['pct']:.2f}% of body weight a week"
         + (", right on your planned pace." if mom["status"] == "on_pace" else "."))
    a += ("\n\nA single week swings 2–3 lb on water and glycogen alone, so a flat week inside a steady "
          "month is noise. The 4-week line is the one to act on.")
    if e.get("measured") is not None:
        a += f"\n\nThe scale says you're running a deficit of about {e['measured']:,.0f} kcal a day"
        a += (f"; your food and activity logs say {e['logged']:,.0f}. Over weeks, trust the scale."
              if e.get("logged") is not None else ".")
    if pr.get("mean") and target:
        a += f" Protein is {pr['mean']:.0f} g a day against a {target:.0f} g target, which protects muscle on a cut."
    return {"q": "Why did the scale barely move this week?", "a": a}


def conversation(con, day, goals, r, ws, st):
    """[{role, text}] in the chat panel's format, built from today's engine results."""
    pairs = [_call(r), _squat(con, day, goals, st), _scale(con, day, goals, ws)]
    return [m for p in pairs if p for m in ({"role": "user", "text": p["q"]}, {"role": "assistant", "text": p["a"]})]

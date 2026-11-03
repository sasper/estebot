"""Why today's call: the panel's words, built from the engine's results.

Formats only. Every number is the engine's, rounded for reading, and the rule that decided
the day is core.call_rule's, so the panel can't explain a call the engine didn't make.
`default` holds what everyone sees, in plain words; `numbers` holds the ranges, day counts
and the engine's own reason lines, behind "Show the numbers". The default view's words are
tested for jargon (tests/test_call_panel.py).
"""
import re

from estecoach import core

# Row labels, sentence names and the adjective for a flagged day, per area. Training load is
# flagged when it's well above or well below usual, so its days are "unusual-load", and how
# you feel covers the back as well as energy, so "rough check-in".
LABEL = {"sleep": "Sleep", "recovery": "HRV & resting heart rate", "feel": "How you feel",
         "load": "Training load", "effort": "Effort", "nutrition": "Nutrition",
         "pace": "Weight-loss pace"}
NAME = {"sleep": "sleep", "recovery": "recovery", "feel": "check-in", "load": "training load",
        "effort": "effort", "nutrition": "eating", "pace": "weight-loss pace"}
ADJ = {"sleep": "poor-sleep", "recovery": "low-recovery", "feel": "rough check-in",
       "load": "unusual-load", "effort": "high-effort", "nutrition": "under-fueled",
       "pace": "fast-loss"}
STATUS = {"green": "No concern", "amber": "Watch", "red": "Concern", None: "No reading yet"}
VERDICT = {"hard": "Go hard", "normal": "Go as planned", "light": "Take it easy", "rest": "Rest"}
HRV_TIP = "Heart rate variability: the variation between heartbeats. Higher usually means better recovered."
TOO_FEW = "Too few days to judge"
PAIN_SHORT = {1: "noticeable", 2: "limits some movements", 3: "hurts to move"}


# ---------------------------------------------------------------- rounding for reading
def signed(x):
    """A whole number with its sign, never "-0"."""
    n = round(x)
    return "0" if n == 0 else f"{n:+d}"


def about(x, step=10):
    """2,343 -> "about 2,340"."""
    return f"about {round(x / step) * step:,.0f}"


def one(x):
    """One decimal, without a trailing .0: 7.0 -> "7", 7.25 -> "7.2"."""
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


def tens(diff):
    """A share-of-targets difference as whole targets in every 10, rounded."""
    return round(abs(diff) * 10)


def effect(e, adj):
    """The default view's line for an area's record: fewer or more of every 10 targets."""
    if not e or e.get("diff") is None:
        return TOO_FEW
    n = tens(e["diff"])
    if n == 0:
        return f"On {adj} days, no clear difference in targets hit."
    return f"On {adj} days, you hit about {n} {'fewer' if e['diff'] < 0 else 'more'} of every 10 targets than usual."


def effect_numbers(e):
    if not e or e.get("diff") is None:
        n = core.EVIDENCE_MIN_N
        got = f"{days(e['n_flagged'])} with it vs {e['n_clear']} without" if e else "no days yet"
        return f"{got}; {n} of each are needed"
    return (f"{signed(e['diff'] * 100)} pts (likely {signed(e['lo'] * 100)} to {signed(e['hi'] * 100)}), "
            f"{days(e['n_flagged'])} with it vs {e['n_clear']} without")


def days(n):
    return f"{n} day" if n == 1 else f"{n} days"


def _join(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


# ---------------------------------------------------------------- each area, today
def today_line(x, present=False):
    f, a = x.get("facts") or {}, x["area"]
    if a == "sleep":
        if f.get("last") is None and f.get("three") is None:
            return "No sleep recorded for last night"
        parts = []
        if f.get("last") is not None:
            parts.append(f"{one(f['last'])} hours last night")
        if f.get("three") is not None:
            parts.append(f"{one(f['three'])} a night over the last 3 nights vs {one(f['base'])} usual")
        return "; ".join(parts)
    if a == "recovery":
        parts = []
        if f.get("hrv") is not None:
            parts.append(f"Heart rate variability {f['hrv']:.0f} this week vs {f['hrv_usual']:.0f} usual")
        if f.get("rhr") is not None:
            parts.append(f"resting heart rate {f['rhr']:.0f} vs {f['rhr_usual']:.0f} usual")
        return "; ".join(parts) or "Not enough readings yet"
    if a == "feel":
        if not f.get("checked_in"):
            return "No check-in yet today"
        parts = []
        if f.get("energy") is not None:
            parts.append(f"Energy {f['energy']} of 3")
        pain = f.get("pain")
        if pain is not None and not present:
            label = dict(core.PAIN_AREAS)
            parts.append("no pain" if not pain else "; ".join(
                f"{label[a].lower()} {PAIN_SHORT[v]}" for a, v in sorted(pain.items(), key=lambda kv: -kv[1])))
        return "Today: " + ", ".join(parts) if parts else "Checked in today"
    if a == "load":
        if f.get("ratio") is None:
            return "Not enough days recorded yet"
        how = ("within your normal range" if f.get("ratio_level") == "green" else
               "a big jump from usual" if f["ratio"] > 1 else "well below usual")
        line = f"{f['week_min']:.0f} minutes of exercise a day this week vs {f['usual_min']:.0f} usual: {how}"
        if f.get("monotony_level") not in (None, "green"):
            line += ". Training days have all been about the same length"
        return line
    if a == "effort":
        if not f.get("enough"):
            return "Not enough rated workouts yet"
        return f"How hard workouts felt: {one(f['week'])} out of 10 this week vs {one(f['before'])} before"
    if a == "nutrition":
        if not f.get("intake") and not f.get("protein"):
            return f"Fewer than 2 of the last {f.get('days', 4)} days fully logged"
        parts = []
        if f.get("intake") and f.get("maintain"):
            parts.append(f"{about(f['intake']).capitalize()} calories a day vs {about(f['maintain'])} to maintain")
        if f.get("protein") and f.get("protein_target"):
            parts.append(f"protein {f['protein']:.0f} g a day vs {f['protein_target']:.0f} g target")
        line = "; ".join(parts)
        if f.get("logged") is not None and f["logged"] < f.get("days", 4):
            line += f" (only {f['logged']} of the last {f['days']} days fully logged)"
        return line
    if a == "pace":
        if f.get("pct") is None:
            return "Not enough weigh-ins yet"
        mx = one(f["max_pct"])
        if f["pct"] < 0:
            return f"Gaining {one(-f['pct'])}% of body weight a week. Only a concern when losing faster than {mx}% a week."
        return f"Losing {one(f['pct'])}% of body weight a week. Only a concern above {mx}% a week, where recovery can suffer."
    return ""


# ---------------------------------------------------------------- the call in a sentence
def _hold_words(h, present):
    if present or h == "a health note":
        return "a health note" if not h.startswith("back from") else "you're coming back after a quiet stretch"
    if h.startswith("pain that limits your "):
        return "you have pain that limits your " + h[len("pain that limits your "):].replace(", which today's workout uses", "") + ", and today's workout uses it"
    return {"injured today": "you're injured today",
            "pain yesterday, and no check-in today": "you had pain that limited movement yesterday, and there's no check-in yet today",
            "injured yesterday, and no clean check-in today": "you were injured yesterday, and there's no check-in yet saying you're fine",
            "sick today or yesterday": "you were sick today or yesterday",
            "back from a near-idle stretch": "you're coming back after a quiet stretch"}.get(h, h)


def _positive_words(r):
    p = r.get("positive") or ""
    return ("you checked in feeling at least OK" if "checked in" in p or "check-in" in p
            else "your last workout was strong")


def verdict_line(r, present=False):
    rule, areas = r.get("rule"), [NAME[a] for a in r.get("rule_areas") or []]
    counted = [NAME[a] for a in r.get("voting") or []]
    head = VERDICT[r["verdict"]] + "."
    why = {
        "pain": ("A health note in today's check-in means rest." if present
                 else "Your check-in says something hurts to move. Rest today. If it isn't improving, get it looked at."),
        "two_red": f"Your {_join(areas)} both show Concern.",
        "one_red": f"Your {_join(areas)} shows Concern.",
        "two_amber": f"Your {_join(areas)} both need watching.",
        "hold": "A safety rule applies: " + "; ".join(_hold_words(h, present) for h in r.get("holds") or []) + ".",
        "earned_hard": f"Nothing that counts for you shows a problem, and {_positive_words(r)}, which has gone with better workouts for you.",
        "one_amber": f"Your {_join(areas)} is worth watching, but one signal isn't enough to ease off.",
        "no_reason": f"Your {_join(counted)} {'looks' if len(counted) == 1 else 'look'} fine, and nothing says today is a day to push.",
        "no_votes": "None of your signals has shown a clear pattern for you yet, so the plan stands.",
    }.get(rule, "")
    return f"{head} {why}".strip()


# ---------------------------------------------------------------- the whole panel
RUNGS = (("rest", "Rest", "Pain that hurts to move in today's check-in, or two counted signals show Concern"),
         ("light", "Take it easy", "One counted signal shows Concern, two show Watch, or a safety rule applies"),
         ("hard", "Go hard", "No counted signal shows Watch or Concern, and something that predicts good days for you is present"),
         ("normal", "Go as planned", "Everything else"))

EXPLAINER = ("EsteCoach watches seven signals. A signal only affects your call once your own history "
             "shows it matters: on days it looked bad, your workouts went worse. Until then, it's "
             "shown here but doesn't count.")

SAFETY = ("These make it a take-it-easy day regardless of other signals: sick today or yesterday, "
          "injured today, injured yesterday with no check-in this morning saying you're fine, "
          "pain that limits movement in an area today's workout uses (or yesterday, with no check-in "
          "yet today), or coming back after a quiet stretch.")
SAFETY_PRESENT = ("These make it a take-it-easy day regardless of other signals: a health note today "
                  "or yesterday, or coming back after a quiet stretch.")

TRACK_EASY_NOTE = ("Since 2 Nov 2026, easy days have lighter targets, so this row covers earlier days. "
                   "Easy days since then are scored on their lighter targets, below, and don't count "
                   "toward which signals matter.")


def build(r, ev, recorded=None, present=False):
    voting = list(r.get("voting") or [])
    areas = ev.get("areas", {}) if ev else {}
    rows = []
    for x in r["signals"]:
        a = x["area"]
        e = areas.get(a)
        rows.append({
            "area": a, "label": LABEL[a], "level": x["level"], "status": STATUS[x["level"]],
            "counted": a in voting, "tip": HRV_TIP if a == "recovery" else None,
            "today": today_line(x, present), "effect": effect(e, ADJ[a]),
            # numbers view
            "reasons": x["reasons"], "effect_numbers": effect_numbers(e),
            "record": (e or {}).get("status", "not enough data")})
    counted = [x for x in rows if x["counted"]]
    watching = [x for x in rows if not x["counted"]]

    strongest = min((x for x in counted if (areas.get(x["area"]) or {}).get("diff") is not None),
                    key=lambda x: areas[x["area"]]["diff"], default=None)
    example = (f"Your clearest example: {strongest['effect'][0].lower()}{strongest['effect'][1:]}" if strongest
               else "None of your signals has shown a clear pattern for you yet.")

    hr_ev = (ev or {}).get("hard_reasons", {})
    reasons = []
    for k, label in (("check-in", "You checked in feeling at least OK" + ("" if present else ", with no pain that limits today's workout")),
                     ("last session", "Strong last workout: at least 8 of every 10 targets hit")):
        e, now = hr_ev.get(k), (r.get("hard_reasons") or {}).get(k)
        if now is None:
            today = "No check-in yet" if k == "check-in" else "No workout yet"
        elif k == "last session":
            today = ("Yes, " if now[0] else "No, ") + re.sub(r"^your last session met", "hit", now[1])
        else:
            today = "Yes" if now[0] else "No"
        if not e or e.get("diff") is None:
            proven = TOO_FEW
        elif e.get("supports"):
            proven = f"Yes, about {tens(e['diff'])} more of every 10 targets hit on those days"
        else:
            proven = "Not yet"
        reasons.append({"label": label, "today": today, "proven": proven,
                        "numbers": effect_numbers(e), "words": now[1] if now else None})

    holds = [_hold_words(h, present) for h in r.get("holds") or []]

    track = []
    verdicts = (ev or {}).get("verdicts", {})
    for v in ("hard", "normal", "light", "rest"):
        got, rec = verdicts.get(v), (recorded or {}).get(v)
        if not got and not rec:
            continue
        enough = got and got["n"] >= core.EVIDENCE_MIN_N
        track.append({"verdict": v, "word": VERDICT[v],
                      "shown": f"{got['hit'] * 100:.0f}% of targets hit" if enough else TOO_FEW,
                      "note": TRACK_EASY_NOTE if v == "light" else None,
                      "replay": got, "recorded": rec})
    lo = (recorded or {}).get("lighter")
    lighter = ({"word": "Take it easy, lighter targets",
                "shown": f"{lo['hit'] * 100:.0f}% of the lighter targets hit over {days(lo['n'])}"} if lo else None)
    light, normal = verdicts.get("light"), verdicts.get("normal")
    if (light and normal and light["n"] >= core.EVIDENCE_MIN_N and normal["n"] >= core.EVIDENCE_MIN_N):
        track_text = ("On days the app would say to take it easy, you hit fewer of your usual targets. "
                      "That suggests it's picking the right days to back off." if light["hit"] < normal["hit"] else
                      "On days the app would say to take it easy, you haven't hit fewer of your usual targets "
                      "so far, so it isn't clear yet that it's picking the right days to back off.")
    else:
        track_text = "There aren't enough workouts yet to tell whether the calls match how they went."

    return {
        "default": {
            "title": "Why today's call", "verdict": verdict_line(r, present),
            "summary": f"{VERDICT[r['verdict']]} · {len(counted)} of {len(rows)} signals count",
            "counted": counted, "watching": watching,
            "rungs": [{"key": k, "word": w, "when": (when.replace("Pain that hurts to move", "A health note") if present else when),
                       "on": r["verdict"] == k} for k, w, when in RUNGS],
            "explainer": EXPLAINER, "example": example,
            "reasons": reasons, "holds": holds, "safety": SAFETY_PRESENT if present else SAFETY,
            "track": track, "track_text": track_text, "lighter": lighter,
        },
        "numbers": {"sessions": (ev or {}).get("sessions", 0), "phase_days": (ev or {}).get("phase_days"),
                    "min_n": core.EVIDENCE_MIN_N},
    }

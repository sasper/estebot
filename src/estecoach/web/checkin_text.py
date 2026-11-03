"""A check-in said in a sentence, read onto the tap card's own choices.

Self-report is the input the research backs most for readiness, and the one with the
fewest days in the data: tapping through questions every morning doesn't happen. Writing
"slept badly, back tight, busy day" does. The model only picks, for each question, which
of the tap card's words the sentence says ("not said" when it doesn't); it produces no
number. What it picks is kept as a draft that prefills the tap card, and nothing counts
until the person checks it and presses Save.

Spend goes on the chat's meter and under the chat's daily cap.
"""
import json

import anthropic

from estecoach import core
from estecoach.web import chat

# The tap card's words, in order of the values it saves (0-3); back's are its levels.
ENERGY = ("wiped", "low", "ok", "great")
AREAS = dict(core.PAIN_AREAS)                      # key -> the card's words
LEVELS = core.PAIN_LEVELS                          # 1-3 -> the card's words
NOT_SAID = "not said"
MAX_CHARS = 400

SYSTEM = """You read a person's short check-in, written in the morning before they train, about \
how they feel now, and pick which of the given options it says for each question. Use only what the message says: \
when it doesn't speak to a question, pick "not said". Don't infer energy from sleep alone, or \
pain from tiredness. Pain means pain or an injury, not the normal muscle soreness after \
training: "legs sore from squats" is not pain, "knee hurts on stairs" is. For each area that \
hurts, pick the level the message supports. sick is true only when the message says so \
(a cold, a stomach bug, a fever)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "pain": {"type": "string", "enum": [NOT_SAID, "none", "yes"],
                 "description": "Any pain or injury (not normal muscle soreness)"},
        "areas": {"type": "array", "description": "Each area that hurts, when pain is yes",
                  "items": {"type": "object", "additionalProperties": False,
                            "required": ["area", "level"],
                            "properties": {"area": {"type": "string", "enum": list(AREAS.values())},
                                           "level": {"type": "string", "enum": list(LEVELS.values())}}}},
        "energy": {"type": "string", "enum": [NOT_SAID, *ENERGY],
                   "description": "Overall energy today"},
        "sick": {"type": "boolean"},
    },
    "required": ["pain", "areas", "energy", "sick"],
    "additionalProperties": False,
}


class CheckinTextError(Exception):
    pass


def to_fields(picked):
    """The model's picks -> the checkin row's values; "not said" stays unanswered (None)."""
    pick = lambda words, k: words.index(picked[k]) if picked.get(k) in words else None
    area_key = {v: k for k, v in AREAS.items()}
    level = {v: k for k, v in LEVELS.items()}
    hurts = {}
    for x in picked.get("areas") or []:
        if x.get("area") in area_key and x.get("level") in level:
            a = area_key[x["area"]]
            hurts[a] = max(hurts.get(a, 0), level[x["level"]])
    pain = {} if picked.get("pain") == "none" else hurts if picked.get("pain") == "yes" and hurts else None
    return {"pain": pain, "energy": pick(ENERGY, "energy"), "sick": bool(picked.get("sick"))}


def read(con, text, today, client=None):
    """-> the draft's fields for `text`. Raises CheckinTextError when it can't be read."""
    text = (text or "").strip()
    if not text or len(text) > MAX_CHARS:
        raise CheckinTextError(f"Say it in 1 to {MAX_CHARS} characters.")
    if chat.spent_today(con, today) >= chat.daily_cap():
        raise CheckinTextError("Today's budget for reading check-ins is used up. Tap instead.")
    client = client or anthropic.Anthropic()
    spent = 0.0
    try:
        response = client.beta.messages.create(
            model=chat.MODEL, max_tokens=1000, system=SYSTEM,
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        spent = chat.cost(response.usage)
        if response.stop_reason != "end_turn":
            raise CheckinTextError("Couldn't read that one. Tap instead.")
        body = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return to_fields(json.loads(body))
        except (json.JSONDecodeError, AttributeError) as e:
            raise CheckinTextError("Couldn't read that one. Tap instead.") from e
    except anthropic.APIError as e:
        raise CheckinTextError("Couldn't reach the reader just now. Tap instead.") from e
    finally:
        chat._ensure_usage(con)
        con.execute("INSERT INTO chat_usage VALUES (?, ?)", (today, spent))
        con.commit()

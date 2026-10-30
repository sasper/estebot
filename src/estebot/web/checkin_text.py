"""A check-in said in a sentence, read onto the tap card's own choices.

Self-report is the input the research backs most for readiness, and the one with the
fewest days in the data: tapping four questions every morning doesn't happen. Writing
"slept badly, back tight, busy day" does. The model only picks, for each question, which
of the tap card's words the sentence says ("not said" when it doesn't); it produces no
number. What it picks is kept as a draft that prefills the tap card, and nothing counts
until the person checks it and presses Save.

Spend goes on the chat's meter and under the chat's daily cap.
"""
import json

import anthropic

from estebot.web import chat

# The tap card's words, in order of the values it saves (0-3); back's are its levels.
ENERGY = ("wiped", "low", "ok", "great")
RECOVERY = ("very slow", "slow", "ok", "normal")
BACK = ("ok", "tired", "sore", "spasm")
NOT_SAID = "not said"
MAX_CHARS = 400

SYSTEM = """You read a person's short morning check-in about how they feel before training, and \
pick which of the given options it says for each question. Use only what the message says: \
when it doesn't speak to a question, pick "not said". Don't infer energy from sleep alone, or \
the back from soreness elsewhere. sick and injured are true only when the message says so \
(a cold, a stomach bug, a fever; a pulled or tweaked muscle, a sprain)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "back": {"type": "string", "enum": [NOT_SAID, *BACK],
                 "description": "Lower back: ok, tired, sore, or spasm"},
        "energy": {"type": "string", "enum": [NOT_SAID, *ENERGY],
                   "description": "Overall energy today"},
        "inter_set_recovery": {"type": "string", "enum": [NOT_SAID, *RECOVERY],
                               "description": "How well they recovered between sets in their last session"},
        "sick": {"type": "boolean"},
        "injured": {"type": "boolean"},
    },
    "required": ["back", "energy", "inter_set_recovery", "sick", "injured"],
    "additionalProperties": False,
}


class CheckinTextError(Exception):
    pass


def to_fields(picked):
    """The model's picks -> the checkin row's values; "not said" stays unanswered (None)."""
    pick = lambda words, k: words.index(picked[k]) if picked.get(k) in words else None
    back = picked.get("back")
    return {"back": back if back in BACK else None,
            "energy": pick(ENERGY, "energy"),
            "inter_set_recovery": pick(RECOVERY, "inter_set_recovery"),
            "sick": bool(picked.get("sick")), "injured": bool(picked.get("injured"))}


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

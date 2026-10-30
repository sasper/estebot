import json
from types import SimpleNamespace as NS

import pytest

TODAY = "2026-10-30"


class FakeClaude:
    """Answers once with the given picks (or stop reason) and keeps the request."""
    def __init__(self, picks=None, stop="end_turn"):
        self.picks, self.stop, self.requests = picks, stop, []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        usage = NS(input_tokens=400, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return NS(stop_reason=self.stop, usage=usage,
                  content=[NS(type="text", text=json.dumps(self.picks or {}))])


def test_the_model_picks_the_cards_words_and_never_a_number(con):
    # Rule 2: no model produces a figure that gets acted on. The schema offers only the tap
    # card's words, and "not said" leaves a question unanswered, as an untapped one is.
    from estebot.web import checkin_text
    fake = FakeClaude({"back": "sore", "energy": "low", "inter_set_recovery": "not said",
                       "sick": False, "injured": False})
    f = checkin_text.read(con, "slept badly, back tight, busy day", TODAY, client=fake)
    assert f == {"back": "sore", "energy": 1, "inter_set_recovery": None, "sick": False, "injured": False}
    schema = fake.requests[0]["output_config"]["format"]["schema"]["properties"]
    assert schema["energy"]["enum"] == ["not said", "wiped", "low", "ok", "great"]
    assert all(p.get("type") in ("string", "boolean") for p in schema.values())


def test_reading_a_check_in_is_metered_and_capped_with_the_chat(con, monkeypatch):
    from estebot.web import chat, checkin_text
    picks = {"back": "not said", "energy": "great", "inter_set_recovery": "ok", "sick": False, "injured": False}
    checkin_text.read(con, "feeling great", TODAY, client=FakeClaude(picks))
    assert chat.spent_today(con, TODAY) > 0
    monkeypatch.setenv("ESTEBOT_CHAT_DAILY_USD", "0")
    with pytest.raises(checkin_text.CheckinTextError):
        checkin_text.read(con, "feeling great", TODAY, client=FakeClaude(picks))


def test_a_refusal_or_an_empty_message_is_not_a_check_in(con):
    from estebot.web import checkin_text
    with pytest.raises(checkin_text.CheckinTextError):
        checkin_text.read(con, "anything", TODAY, client=FakeClaude(stop="refusal"))
    with pytest.raises(checkin_text.CheckinTextError):
        checkin_text.read(con, "   ", TODAY, client=FakeClaude())

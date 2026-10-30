import importlib
from datetime import date, timedelta

import pytest
from starlette.requests import Request

DAY = "2026-03-10"
# Invented text, standing in for the kind of note the page must not show to a visitor.
DOSE_NOTE = "Took the 2 mg zorbotide shot, stomach off all afternoon"
SICK_NOTE = "Sick with a fever, stayed in bed"


@pytest.fixture
def main(con):
    from estebot.web import main as mod
    mod = importlib.reload(mod)
    d = lambda n: (date.fromisoformat(DAY) - timedelta(days=n)).isoformat()
    for n in range(20):
        con.execute("INSERT INTO weight (date, weight_lb) VALUES (?, ?)", (d(n), 250 + n * 0.2))
    for n, note in ((1, SICK_NOTE), (3, DOSE_NOTE)):
        con.execute("INSERT INTO annotation (start_date, type, note, source) VALUES (?, 'note', ?, 'test')",
                    (d(n), note))
    con.execute("""INSERT INTO checkin (date, back, back_reported, energy, note, source, needs_review)
                   VALUES (?, 'sore', 1, 2, ?, 'notes', 1)""", (DAY, DOSE_NOTE))
    con.commit()
    return mod


def page(main, query="", cookie=None):
    headers = [(b"cookie", f"present={cookie}".encode())] if cookie else []
    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": query.encode(),
                   "headers": headers})
    present = dict(x.split("=") for x in query.split("&") if x).get("present")
    resp = main.today(req, DAY, present)
    return resp.body.decode(), resp.headers.get("set-cookie", "")


def test_presentation_mode_leaves_notes_and_doses_out_of_the_html(main):
    # The page is shown to people who should see the training and the weight, not the
    # medical notes or the sick days. Hiding them with CSS would leave them in
    # view-source, so they must not be rendered at all.
    normal, _ = page(main)
    assert DOSE_NOTE in normal and SICK_NOTE in normal
    assert "unwell" in normal

    shown, cookie = page(main, "present=1")
    for private in (DOSE_NOTE, SICK_NOTE, "zorbotide", "fever", "unwell", "back sore",
                    'name="note"'):
        assert private not in shown, private
    assert "present=1" in cookie and "Presentation" in shown
    assert "Body weight" in shown and "Strength</h2>" in shown


def test_the_cookie_keeps_presentation_mode_on_until_it_is_turned_off(main):
    # ?day= links and the redirect after a form post carry no query flag; the cookie does.
    assert DOSE_NOTE not in page(main, cookie="1")[0]
    shown, cookie = page(main, "present=0", cookie="1")
    assert DOSE_NOTE in shown and "present=0" in cookie


def test_private_predictions_are_dropped_and_back_status_leaves_the_feel_signal(main):
    ctx = {"r": {"signals": [{"area": "feel", "level": "amber", "reasons": ["back sore today"]},
                             {"area": "sleep", "level": "green", "reasons": ["7.5 h asleep"]}]},
           "checkin": None, "plan": None, "calendar": [], "notes": [],
           "nut": {"supplements": ["Fish oil"], "gaps": [{"nutrient": "Iron", "note": "x"}],
                   "near_limit": [{"nutrient": "Zinc"}]},
           "preds": [{"id": "P-1", "title": "Strength holds through the cut"},
                     {"id": "P-2", "title": "A personal claim", "private": True},
                     {"id": "P-3", "title": "HRV rises", "private": False}]}
    c = main.presentable(ctx)
    assert [p["id"] for p in c["preds"]] == ["P-1", "P-3"]
    assert c["r"]["signals"][0]["reasons"] == ["from the daily check-in"]
    assert c["r"]["signals"][1]["reasons"] == ["7.5 h asleep"]
    assert c["nut"]["supplements"] == [] and c["nut"]["near_limit"] == []
    assert c["nut"]["gaps"][0]["note"] is None
    assert ctx["r"]["signals"][0]["reasons"] == ["back sore today"]     # the original is untouched


def test_the_public_demo_is_read_only(main, con, monkeypatch):
    # The demo is one page shared by every visitor; one of them setting a goal or answering
    # a check-in would change it for everyone else.
    normal, _ = page(main)
    assert 'action="/goal"' in normal and 'action="/checkin"' in normal

    monkeypatch.setenv("ESTEBOT_DEMO", "1")
    shown, _ = page(main)
    assert 'action="/goal"' not in shown and 'action="/checkin"' not in shown
    assert "Needs an answer" not in shown and "Goal:" in shown
    assert "present=1" not in shown          # nothing private on the demo, so no toggle

    assert main.set_goal(None, 150.0, "").status_code == 403
    assert main.checkin(None, DAY, "ok", 0, 3, 3, "").status_code == 403
    assert con.execute("SELECT COUNT(*) FROM setting").fetchone()[0] == 0
    assert con.execute("SELECT source FROM checkin WHERE date=?", (DAY,)).fetchone()[0] == "notes"


TAP_NOTE = "Slept badly, legs heavy"     # invented


def save(main, day, back=None, energy=None, recovery=None, note="", sick=False, injured=False):
    # Called directly, a route's unset parameters are Form() objects, so pass every one.
    return main.checkin(None, day, back, 0, energy, recovery, note, sick, injured)


def today_page(main, query=""):
    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": query.encode(),
                   "headers": []})
    present = dict(x.split("=") for x in query.split("&") if x).get("present")
    return main.today(req, date.today().isoformat(), present).body.decode()


def card(html):
    return html.split('id="checkin"')[1].split("</form>")[0]


def test_the_tap_card_is_there_every_day_without_a_note(main):
    # A phone-only user never writes a note, so the check-in cannot wait for one to need
    # review. Nothing is preselected: an untapped back must not save as "back ok".
    html = today_page(main)
    assert 'action="/checkin"' in html and 'type="radio" name="energy"' in html
    assert "checked" not in card(html)


def test_a_tap_check_in_is_saved_prefilled_and_read_by_the_verdict(main, con):
    from estebot.analysis import engine
    today = date.today().isoformat()
    assert save(main, today, "sore", 1, 2, TAP_NOTE).status_code == 303
    row = con.execute("SELECT * FROM checkin WHERE date=?", (today,)).fetchone()
    assert (row["source"], row["back"], row["back_reported"], row["energy"],
            row["inter_set_recovery"], row["note"], row["sick"]) == ("manual", "sore", 1, 1, 2, TAP_NOTE, 0)
    feel = next(x for x in engine.readiness(con, today, {})["signals"] if x["area"] == "feel")
    assert feel["level"] == "amber" and "energy 1/3 today" in feel["reasons"]

    html = today_page(main)
    assert 'value="sore" checked' in card(html) and 'name="energy" value="1" checked' in card(html)
    assert TAP_NOTE in card(html) and "saved, tap to change" in html


def test_a_sick_tap_with_no_energy_reads_as_energy_zero_and_rings_the_calendar(main, con):
    # The same rule as a sick note (core.checkin_energy), so being sick cannot turn the feel
    # signal red from a note and leave it empty from a tap.
    from estebot.analysis import engine
    today = date.today().isoformat()
    save(main, today, sick=True)
    row = con.execute("SELECT * FROM checkin WHERE date=?", (today,)).fetchone()
    assert (row["energy"], row["sick"], row["injured"], row["back_reported"]) == (0, 1, 0, 0)
    feel = next(x for x in engine.readiness(con, today, {})["signals"] if x["area"] == "feel")
    assert feel["level"] == "red" and not any("back" in t for t in feel["reasons"])
    assert engine.unwell_days(con, today, today)[today]["sick"]
    assert f'aria-label="{today}: rest day, sick"' in today_page(main)


def test_an_energy_answer_given_with_the_sick_tap_stands(main, con):
    today = date.today().isoformat()
    save(main, today, energy=3, sick=True, injured=True)
    row = con.execute("SELECT energy, sick, injured FROM checkin WHERE date=?", (today,)).fetchone()
    assert tuple(row) == (3, 1, 1)


def test_saving_the_card_answers_a_note_that_needed_review(main, con):
    # The fixture's day has a derived row flagged for review. The card replaces the old
    # review-only form and is prefilled from that row.
    html, _ = page(main)
    assert "Check this." in html and 'value="sore" checked' in card(html)
    save(main, DAY, "tired", 2, 2, "")
    row = con.execute("SELECT * FROM checkin WHERE date=?", (DAY,)).fetchone()
    assert (row["source"], row["needs_review"], row["back"]) == ("manual", 0, "tired")
    assert 'id="checkin"' not in page(main)[0]   # a past day, now answered: no card


def test_presentation_and_demo_modes_leave_the_tap_card_out(main, con, monkeypatch):
    today = date.today().isoformat()
    save(main, today, "spasm", 1, 1, TAP_NOTE, sick=True, injured=True)
    normal = today_page(main)
    assert TAP_NOTE in normal and "injured" in normal

    shown = today_page(main, "present=1")
    for private in ('action="/checkin"', TAP_NOTE, 'name="sick"', "spasm", "injured", "sick", "unwell"):
        assert private not in shown, private

    monkeypatch.setenv("ESTEBOT_DEMO", "1")
    shown = today_page(main, "present=0")
    assert 'action="/checkin"' not in shown and 'id="checkin"' not in shown
    assert save(main, today, "ok", 3, 3, "").status_code == 403
    assert con.execute("SELECT back FROM checkin WHERE date=?", (today,)).fetchone()[0] == "spasm"


def test_needs_an_answer_lists_only_the_last_week(main, con):
    # A conflicting note from two months back stayed in the banner for good, though
    # answering it could no longer change any call on the page.
    old = (date.fromisoformat(DAY) - timedelta(days=60)).isoformat()
    con.execute("""INSERT INTO checkin (date, energy, note, source, needs_review)
                   VALUES (?, 2, 'poor sleep but felt strong', 'notes', 1)""", (old,))
    con.commit()
    banner = page(main)[0].split("Needs an answer")[1].split("</p>")[0]
    assert f"?day={DAY}" in banner and f"?day={old}" not in banner


def test_the_chat_comes_back_after_a_reload_and_stays_out_of_presentation_mode(main, con, monkeypatch):
    # The conversation lived in the tab, so a reload or a change of day lost an answer and
    # the question had to be asked again. Saved answers can quote anything in the data, so
    # presentation mode must not render them.
    from estebot.web import chat
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    chat.save(con, "How did my squat go?", "Up 10 lb on the zorbotide week.", DAY)
    normal, _ = page(main)
    assert "How did my squat go?" in normal and "Up 10 lb" in normal
    shown, _ = page(main, "present=1")
    assert "How did my squat go?" not in shown and "zorbotide" not in shown


def test_the_demo_shows_example_answers_built_from_the_days_numbers(main, monkeypatch):
    # The chat is off in the demo, so its panel shows examples instead. The demo person is
    # regenerated daily, so the answers are filled from the engine: the score in the
    # example is the one on the card beside it.
    monkeypatch.setenv("ESTEBOT_DEMO", "1")
    html, _ = page(main)
    assert "behind today" in html
    score = html.split('class="rscore')[1].split(">")[1].split("<")[0]
    assert f"readiness {score}." in html
    monkeypatch.delenv("ESTEBOT_DEMO")
    assert "behind today" not in page(main)[0]


def test_the_example_call_reads_right_when_nothing_is_flagged():
    # Written against a take-it-easy day, the example said "The other 7 signals are good"
    # and explained a strained signal on a day with none.
    from estebot.web import examples
    sig = lambda area, level, reason: {"area": area, "level": level, "reasons": [reason]}
    green = [sig(a, "green", f"{a} fine") for a in examples.AREA]
    a = examples._call({"verdict": "hard", "score": 100, "signals": green, "voting": ["sleep"],
                        "positive": "your last session met 5 of 5 targets"})["a"]
    assert a.startswith("Push") and "holding you back" not in a and "Flagged but not counted" not in a
    a = examples._call({"verdict": "light", "score": 46, "voting": ["load"],
                        "signals": [sig("load", "red", "too alike")] + green[1:]})["a"]
    assert "holding you back" in a and "too alike" in a
    # a flagged area with no vote is named as such, never as what held the day back
    a = examples._call({"verdict": "normal", "score": 60, "voting": [],
                        "signals": [sig("load", "red", "too alike")] + green[1:]})["a"]
    assert "holding you back" not in a and "Flagged but not counted: training load" in a

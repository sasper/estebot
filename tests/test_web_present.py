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
    assert "Body weight" in shown and "Strength standards" in shown


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

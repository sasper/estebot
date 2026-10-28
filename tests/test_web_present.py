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

    assert main.set_goal(150.0, "").status_code == 403
    assert main.checkin(DAY, "ok", 0, 3, 3, "").status_code == 403
    assert con.execute("SELECT COUNT(*) FROM setting").fetchone()[0] == 0
    assert con.execute("SELECT source FROM checkin WHERE date=?", (DAY,)).fetchone()[0] == "notes"

import asyncio, logging
from dataclasses import replace

import pytest
from starlette.requests import Request

from estecoach.web import signups


@pytest.fixture
def main(con):
    import importlib
    from estecoach.web import main as mod
    return importlib.reload(mod)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTECOACH_SIGNUPS", str(tmp_path / "signups.sqlite"))
    signups._recent.clear()
    return tmp_path


def _post(main, form, ip="203.0.113.7"):
    import urllib.parse
    body = urllib.parse.urlencode(form).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    r = Request({"type": "http", "method": "POST", "path": "/signup", "query_string": b"",
                 "headers": [(b"content-type", b"application/x-www-form-urlencoded"),
                             (b"fly-client-ip", ip.encode())]}, receive)
    return asyncio.run(main.signup_submit(r))


GOOD = {"name": "Ada Lift", "email": "ada@example.com", "tracks": "Lifts in Hevy"}


def test_a_request_is_kept_and_its_details_never_reach_the_log(main, store, caplog):
    with caplog.at_level(logging.WARNING):
        r = _post(main, GOOD)
    assert r.status_code == 200 and "Thanks, Ada." in r.body.decode()
    (row,) = signups.listing()
    assert (row["email"], row["tracks"]) == ("ada@example.com", "Lifts in Hevy")
    assert set(row) == {"id", "at", "name", "email", "tracks", "reviewed_at"}      # nothing else is kept
    assert "ada@example.com" not in caplog.text and "Ada" not in caplog.text


def test_a_bot_filling_the_hidden_field_is_thanked_and_nothing_is_kept(main, store):
    assert _post(main, dict(GOOD, website="http://spam.example")).status_code == 200
    assert signups.listing() == []


def test_a_bad_email_is_sent_back_with_what_to_fix(main, store):
    r = _post(main, dict(GOOD, email="not-an-email"))
    assert r.status_code == 400 and "email doesn" in r.body.decode() and signups.listing() == []


def test_one_address_gets_a_few_requests_an_hour(main, store):
    codes = [_post(main, dict(GOOD, email=f"a{i}@example.com")).status_code for i in range(signups.PER_HOUR + 1)]
    assert codes[-1] == 429 and len(signups.listing()) == signups.PER_HOUR
    assert _post(main, GOOD, ip="198.51.100.9").status_code == 200       # someone else isn't blocked


def test_only_the_owner_can_read_the_requests(main, store, monkeypatch):
    _post(main, GOOD)
    me = main.people.for_request(None)
    req = Request({"type": "http", "method": "GET", "path": "/signups", "query_string": b"", "headers": []})
    def as_person(person):
        monkeypatch.setattr(main.people, "for_request", lambda request: person)
        return main.signup_review(req)
    assert as_person(me).status_code == 404                                  # signed out
    owner_cfg = replace(me.cfg, site={"review_signups": True})
    assert as_person(replace(me, email="me@example.com", cfg=owner_cfg)).status_code == 200
    assert "ada@example.com" in as_person(replace(me, email="me@example.com", cfg=owner_cfg)).body.decode()
    assert as_person(replace(me, email="other@example.com")).status_code == 404   # signed in, not the owner
    monkeypatch.setenv("ESTECOACH_DEMO", "1")
    assert as_person(replace(me, email="me@example.com", cfg=owner_cfg)).status_code == 404


def test_the_demo_never_keeps_requests_in_its_own_folder(store):
    # Requests sit in $ESTECOACH_SIGNUPS (the volume), not the demo's folder, which is
    # deleted and written again at every machine start.
    assert signups.path() == store / "signups.sqlite"



def test_the_form_asks_for_no_health_details_and_says_where_it_goes(main):
    req = Request({"type": "http", "method": "GET", "path": "/signup", "query_string": b"", "headers": []})
    html = main.signup_form(req).body.decode()
    names = set(__import__("re").findall(r'<(?:input|textarea|select)[^>]*\bname="(\w+)"', html))
    assert names == {"name", "email", "tracks", "website"}                 # website is the bot trap
    assert "Comes to me. Not shared, not sold." in html and 'href="/privacy"' in html
    page = main.privacy(Request({"type": "http", "method": "GET", "path": "/privacy", "query_string": b"", "headers": []}))
    assert page.status_code == 200 and "isn't shared, sold" in page.body.decode()

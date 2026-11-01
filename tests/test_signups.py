import asyncio, importlib, logging, urllib.parse
from dataclasses import replace

import pytest
from starlette.requests import Request

from estecoach.web import signups


@pytest.fixture
def main(con):
    from estecoach.web import main as mod
    return importlib.reload(mod)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ESTECOACH_SIGNUPS", str(tmp_path / "signups.sqlite"))
    monkeypatch.setattr(signups, "notify", lambda row, new: None)
    signups._recent.clear()
    return tmp_path


def _post(main, pairs, ip="203.0.113.7", origin="https://estecoach.app", json=True, body=None):
    body = body if body is not None else urllib.parse.urlencode(pairs).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    headers = [(b"content-type", b"application/x-www-form-urlencoded"), (b"host", b"estecoach.app"),
               (b"fly-client-ip", ip.encode()), (b"content-length", str(len(body)).encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if json:
        headers.append((b"accept", b"application/json"))
    r = Request({"type": "http", "method": "POST", "path": "/signup", "query_string": b"", "headers": headers}, receive)
    return asyncio.run(main.signup_submit(r))


GOOD = [("name", "Ada Lift"), ("email", "Ada@Example.com"), ("tracks", "Hevy"), ("tracks", "Apple Watch"),
        ("note", "Training for a meet")]


def test_a_valid_request_is_stored_and_its_details_never_reach_the_log(main, store, caplog):
    with caplog.at_level(logging.WARNING):
        r = _post(main, GOOD)
    assert r.status_code == 200 and b"Got it. I'll be in touch." in r.body
    (row,) = signups.listing()
    assert (row["email"], row["tracks"], row["note"]) == ("ada@example.com", "Apple Watch, Hevy", "Training for a meet")
    assert "ada@example.com" not in caplog.text.lower() and "Ada" not in caplog.text


def test_a_repeated_email_updates_its_row(main, store):
    _post(main, GOOD)
    _post(main, [("email", "ada@example.com"), ("note", "Now with Cronometer"), ("tracks", "Cronometer")])
    (row,) = signups.listing()
    assert row["note"] == "Now with Cronometer" and row["tracks"] == "Cronometer"
    assert row["name"] == "Ada Lift" and row["updated_at"]


def test_the_honeypot_and_the_rate_limit_refuse(main, store):
    assert _post(main, GOOD + [("website", "http://spam.example")]).status_code == 200
    assert signups.listing() == []                                  # thanked, nothing kept
    codes = [_post(main, [("email", f"a{i}@example.com")]).status_code for i in range(signups.PER_HOUR + 1)]
    assert codes[-1] == 429 and len(signups.listing()) == signups.PER_HOUR
    assert _post(main, GOOD, ip="198.51.100.9").status_code == 200   # someone else isn't blocked


def test_a_bad_email_another_site_and_a_huge_body_are_refused(main, store):
    assert _post(main, [("email", "not-an-email")]).status_code == 400
    assert _post(main, GOOD, origin="https://evil.example").status_code == 403
    assert _post(main, GOOD, origin=None).status_code == 403
    assert _post(main, GOOD, body=b"email=a%40b.co&note=" + b"x" * signups.MAX_BODY).status_code == 413
    assert signups.listing() == []


def test_without_javascript_the_page_comes_back_with_the_answer(main, store, monkeypatch):
    monkeypatch.setattr(main.landing, "demo_context", lambda request, fn: {})
    r = _post(main, GOOD, json=False)
    assert r.status_code == 200 and "Got it. I'll be in touch." in r.body.decode()


def test_admin_signups_refuses_without_sign_in(main, store, monkeypatch):
    _post(main, GOOD)
    me = main.people.for_request(None)
    req = Request({"type": "http", "method": "GET", "path": "/admin/signups", "query_string": b"", "headers": []})
    def as_person(person, route=main.admin_signups):
        monkeypatch.setattr(main.people, "for_request", lambda request: person)
        return route(req)
    owner = replace(me, email="me@example.com", cfg=replace(me.cfg, site={"review_signups": True}))
    assert as_person(me).status_code == 404                                  # signed out
    assert as_person(replace(me, email="other@example.com")).status_code == 404   # not the owner
    assert as_person(me, main.admin_signups_csv).status_code == 404
    assert "ada@example.com" in as_person(owner).body.decode()
    csv = as_person(owner, main.admin_signups_csv)
    assert csv.media_type == "text/csv" and b"ada@example.com" in csv.body
    monkeypatch.setenv("ESTECOACH_DEMO", "1")
    assert as_person(owner).status_code == 404


def test_the_landing_page_shares_as_the_card(main, store, monkeypatch):
    monkeypatch.setattr(main.landing, "demo_context", lambda request, fn: {})
    monkeypatch.setenv("ESTECOACH_DEMO", "1")
    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []})
    html = main.today(req, None, None).body.decode()
    assert 'property="og:image" content="https://estecoach.app/static/social-card.png"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert "<title>EsteCoach: a fitness coach that's seen every set you've lifted</title>" in html
    names = set(__import__("re").findall(r'<(?:input|textarea|select)[^>]*\bname="(\w+)"', html))
    assert names == {"name", "email", "tracks", "note", "website"}      # no health questions
    assert "Comes to me. Not shared, not sold." in html and "Not medical advice" in html


def test_old_demo_links_go_to_demo_and_visits_are_counted(main, store, monkeypatch):
    monkeypatch.setattr(main.landing, "demo_context", lambda request, fn: {})
    monkeypatch.setenv("ESTECOACH_DEMO", "1")
    old = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"day=2026-10-01", "headers": []})
    r = main.today(old, "2026-10-01", None)
    assert r.status_code == 301 and r.headers["location"] == "/demo?day=2026-10-01"
    main.today(Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []}), None, None)
    assert signups.visits()[0]["n"] == 1

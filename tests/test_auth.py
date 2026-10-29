import importlib, os, time, urllib.parse
from http.cookies import SimpleCookie

import pytest
from starlette.requests import Request

DAY = "2026-03-10"
CLIENT_ID = "test-client.apps.googleusercontent.com"
ME = "me@example.com"


def _config(tmp_path, name, weight):
    data = tmp_path / name
    data.mkdir()
    cfg = tmp_path / f"{name}.toml"
    cfg.write_text(f'data_dir = "{data}"\n[goals]\ntarget_weight_lb = {weight - 20}\n')
    return str(cfg)


@pytest.fixture
def site(con, tmp_path, monkeypatch):
    """Sign-in on: one allowed user, and a demo for everyone else. Google is faked."""
    users = tmp_path / "users.toml"
    users.write_text(f'demo_config = "{_config(tmp_path, "demo", 200)}"\n'
                     f'[users]\n"{ME}" = "{_config(tmp_path, "me", 150)}"\n')
    monkeypatch.setenv("ESTEBOT_USERS", str(users))
    monkeypatch.setenv("ESTEBOT_SESSION_KEY", "k" * 40)
    monkeypatch.setenv("ESTEBOT_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ESTEBOT_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ESTEBOT_BASE_URL", "https://estebot.example")
    from estebot.web import auth, main
    auth._settings.cache_clear()
    main = importlib.reload(main)
    google = {}                            # what the fake token endpoint will claim
    def exchange(s, code, verifier):
        assert code == "the-code" and verifier
        return dict({"iss": "https://accounts.google.com", "aud": CLIENT_ID,
                     "exp": time.time() + 300, "email": ME, "email_verified": True,
                     "nonce": google.pop("nonce")}, **google)
    monkeypatch.setattr(auth, "_exchange", exchange)
    yield main, auth, google
    auth._settings.cache_clear()


def req(path="/", cookies=None, query=""):
    hdr = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(),
                    "headers": [(b"cookie", hdr.encode())] if hdr else []})

def cookies(resp):
    out = {}
    for h in resp.headers.getlist("set-cookie"):
        c = SimpleCookie(); c.load(h)
        out.update({k: m.value for k, m in c.items()})
    return out

def sign_in(main, auth, google, **claims):
    """Run the round trip; -> the callback's response."""
    start = main.login()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    flow = cookies(start)[auth.FLOW_COOKIE]
    google["nonce"] = auth.unsign(auth.settings().session_key, "flow", flow)["nonce"]
    google.update(claims)
    return main.callback(req("/auth/callback", {auth.FLOW_COOKIE: flow}), "the-code", q["state"][0])


def test_the_login_redirect_carries_state_nonce_and_a_pkce_challenge(site):
    main, auth, _ = site
    resp = main.login()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["location"]).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == ["https://estebot.example/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert "Secure" in resp.headers["set-cookie"] and "HttpOnly" in resp.headers["set-cookie"]


def test_signing_in_shows_your_own_page_and_visitors_get_the_read_only_demo(site):
    main, auth, google = site
    demo = main.today(req(), DAY, None).body.decode()
    assert "Sign in" in demo and 'action="/goal"' not in demo
    assert main.set_goal(req(), 100.0, "").status_code == 403

    resp = sign_in(main, auth, google)
    assert resp.status_code == 303
    session = cookies(resp)[auth.SESSION_COOKIE]
    mine = main.today(req("/", {auth.SESSION_COOKIE: session}), DAY, None).body.decode()
    assert "Sign out" in mine and 'action="/goal"' in mine
    assert 'value="130"' in mine                       # my goal (150 - 20), not the demo's 180


def test_a_form_without_the_sessions_csrf_token_is_refused(site):
    main, auth, google = site
    session = cookies(sign_in(main, auth, google))[auth.SESSION_COOKIE]
    r = req("/goal", {auth.SESSION_COOKIE: session})
    assert main.set_goal(r, 140.0, "", "").status_code == 403
    assert main.set_goal(r, 140.0, "", "forged").status_code == 403
    page = main.today(req("/", {auth.SESSION_COOKIE: session}), DAY, None).body.decode()
    token = page.split('name="csrf" value="')[1].split('"')[0]
    assert main.set_goal(r, 140.0, "", token).status_code == 303


@pytest.mark.parametrize("claims, status", [
    ({"aud": "someone-elses-client"}, 400),
    ({"iss": "https://evil.example"}, 400),
    ({"exp": time.time() - 1}, 400),
    ({"email_verified": False}, 400),
    ({"email": "stranger@example.com"}, 403),        # real Google account, not on the list
])
def test_google_answers_that_dont_check_out_are_refused(site, claims, status):
    main, auth, google = site
    resp = sign_in(main, auth, google, **claims)
    assert resp.status_code == status and auth.SESSION_COOKIE not in cookies(resp)


def test_a_callback_that_did_not_start_here_is_refused(site):
    main, auth, _ = site
    assert main.callback(req("/auth/callback"), "the-code", "any-state").status_code == 400
    flow = cookies(main.login())[auth.FLOW_COOKIE]
    wrong_state = main.callback(req("/auth/callback", {auth.FLOW_COOKIE: flow}), "the-code", "nope")
    assert wrong_state.status_code == 400


def test_a_tampered_or_repurposed_cookie_is_ignored(site):
    main, auth, google = site
    session = cookies(sign_in(main, auth, google))[auth.SESSION_COOKIE]
    body, mac = session.split(".")
    forged = auth._b64(auth._unb64(body).replace(b"me@", b"xx@")) + "." + mac
    assert "Sign in" in main.today(req("/", {auth.SESSION_COOKIE: forged}), DAY, None).body.decode()
    # A flow cookie is signed too, but for another purpose: it can't stand in for a session.
    flow = cookies(main.login())[auth.FLOW_COOKIE]
    assert "Sign in" in main.today(req("/", {auth.SESSION_COOKIE: flow}), DAY, None).body.decode()


def test_removing_someone_from_the_users_file_signs_them_out(site, tmp_path):
    main, auth, google = site
    session = cookies(sign_in(main, auth, google))[auth.SESSION_COOKIE]
    users = tmp_path / "users.toml"
    users.write_text(users.read_text().split("[users]")[0] + "[users]\n")
    os.utime(users, ns=(time.time_ns(), time.time_ns() + 10**9))   # no restart, no cache clear
    assert "Sign in" in main.today(req("/", {auth.SESSION_COOKIE: session}), DAY, None).body.decode()


def _upload(main, token, payload):
    import asyncio, json as _json
    body = _json.dumps(payload).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    hdr = [(b"authorization", f"Bearer {token}".encode())] if token else []
    r = Request({"type": "http", "method": "POST", "path": "/api/health-auto-export",
                 "query_string": b"", "headers": hdr}, receive)
    return asyncio.run(main.health_auto_export(r))


def test_a_phone_upload_needs_its_token_and_lands_where_the_feed_parser_reads(site, tmp_path, monkeypatch):
    # Health Auto Export posts its JSON here instead of writing to iCloud. The token is the
    # only credential, so the users file keeps just its hash.
    import hashlib, json as _json
    main, auth, _ = site
    token = "t" * 40
    cfg = tmp_path / "me.toml"
    feed = tmp_path / "me" / "raw" / "hae"
    cfg.write_text(cfg.read_text() + f'[sources.health_auto_export]\nfrom_date = "2026-01-01"\n'
                   f'metrics_glob = "{feed}/metrics/*.json"\nworkouts_glob = "{feed}/workouts/*.json"\n')
    users = tmp_path / "users.toml"
    users.write_text(users.read_text() + f'[uploads]\n"{hashlib.sha256(token.encode()).hexdigest()}" = "{cfg}"\n')
    auth._settings.cache_clear()
    started = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda args, **kw: started.append((args, kw["env"])))
    payload = {"data": {"metrics": [{"name": "step_count", "units": "count",
                                     "data": [{"date": "2026-03-09 23:58:00 -0400", "qty": 10}] +
                                              [{"date": f"2026-03-10 0{h}:01:00 -0400", "qty": 5}
                                               for h in range(3)]}]}}

    assert _upload(main, None, payload).status_code == 401
    assert _upload(main, "x" * 40, payload).status_code == 401
    assert started == []

    resp = _upload(main, token, payload)
    assert resp.status_code == 202
    # The day most of its points are on, in the phone's time: not UTC's, and not the night before.
    saved = feed / "metrics" / "HealthAutoExport-2026-03-10.json"
    assert _json.loads(saved.read_text())["data"]["metrics"][0]["name"] == "step_count"
    assert not (feed / "workouts").exists()
    assert saved.read_bytes() == _json.dumps(payload).encode()        # stored as sent
    workouts = {"data": {"workouts": [{"id": "w1", "start": "2026-03-10 07:00:00 -0400"}]}}
    assert _upload(main, token, workouts).status_code == 202
    assert (feed / "workouts" / "HealthAutoExport-2026-03-10.json").exists()
    assert _upload(main, token, {"not": "a feed"}).status_code == 400
    (args, env), _ = started
    assert args[-2:] == ["estebot.daily", "--feed"] and env["ESTEBOT_CONFIG"] == str(cfg)

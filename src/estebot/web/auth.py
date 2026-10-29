"""Signing in with Google, and the signed cookies that remember who signed in.

Off unless $ESTEBOT_USERS points at a users file. Without one the page is a single-person
page with no sign-in, which is how it runs on my own machine. The users file maps each
allowed email to that person's config, and names the config anonymous visitors see:

    demo_config = "/data/demo/estebot.toml"
    [users]
    "someone@example.com" = "/data/someone/estebot.toml"

The flow is OAuth 2.0 authorization code with PKCE, plus OpenID Connect's state and nonce.
The ID token comes straight from Google's token endpoint over TLS in exchange for the code
and the client secret, so its signature doesn't need checking here (OpenID Connect Core
3.1.3.7); its claims still do: issuer, audience, expiry, nonce, verified email.

Cookies are signed with HMAC-SHA256 under $ESTEBOT_SESSION_KEY, and each kind of cookie is
signed for its own purpose, so a value issued as one kind can't be replayed as another.
Nothing here needs a dependency beyond the standard library.
"""
import base64, hashlib, hmac, json, os, secrets, time, tomllib, urllib.parse, urllib.request
from dataclasses import dataclass
from functools import lru_cache

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

SESSION_COOKIE, SESSION_DAYS = "estebot_session", 30
FLOW_COOKIE, FLOW_SECONDS = "estebot_signin", 600        # the round trip through Google


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    session_key: bytes
    base_url: str                  # what Google redirects back to; must match the console
    demo_config: str | None
    users: dict                    # lower-cased email -> config path

    @property
    def secure(self):
        return self.base_url.startswith("https://")

    @property
    def redirect_uri(self):
        return self.base_url.rstrip("/") + "/auth/callback"


def settings():
    """None when sign-in is off. Misconfiguration fails loudly rather than opening up. Read
    again whenever the users file changes, so removing someone takes effect at once."""
    path = os.environ.get("ESTEBOT_USERS")
    return _settings(path, os.stat(path).st_mtime_ns) if path else None

@lru_cache(maxsize=4)
def _settings(path, _mtime):
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    key = os.environ.get("ESTEBOT_SESSION_KEY", "")
    if len(key) < 32:
        raise RuntimeError("ESTEBOT_SESSION_KEY must be set, 32 characters or more")
    return Settings(client_id=os.environ["ESTEBOT_GOOGLE_CLIENT_ID"],
                    client_secret=os.environ["ESTEBOT_GOOGLE_CLIENT_SECRET"],
                    session_key=key.encode(),
                    base_url=os.environ.get("ESTEBOT_BASE_URL", "http://127.0.0.1:8742"),
                    demo_config=raw.get("demo_config"),
                    users={e.lower(): p for e, p in raw.get("users", {}).items()})


# ---------------------------------------------------------------- signed values

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def sign(key, purpose, payload, seconds):
    body = _b64(json.dumps(dict(payload, exp=int(time.time()) + seconds)).encode())
    mac = hmac.new(key, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"

def unsign(key, purpose, token):
    """The payload, or None if the value was altered, made for another purpose, or expired."""
    try:
        body, mac = (token or "").split(".")
        want = hmac.new(key, f"{purpose}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if payload.get("exp", 0) > time.time() else None


# ---------------------------------------------------------------- who is this

def session(request, s):
    """{"email", "sid"} for a signed-in request whose email is still allowed, else None.
    Taking someone off the users file signs them out on their next request."""
    data = unsign(s.session_key, "session", request.cookies.get(SESSION_COOKIE))
    return data if data and data.get("email") in s.users else None

def csrf_token(s, sess):
    """Tied to the session, so a form can't be posted from another site with the user's
    cookie (SameSite=Lax already stops most of that; this covers the rest)."""
    return _b64(hmac.new(s.session_key, f"csrf.{sess['sid']}".encode(), hashlib.sha256).digest())

def csrf_ok(s, sess, token):
    return bool(sess and token) and hmac.compare_digest(csrf_token(s, sess), token)


# ---------------------------------------------------------------- the Google round trip

def start(s):
    """-> (Google URL to send the browser to, signed flow cookie value)."""
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": s.client_id, "redirect_uri": s.redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"})
    flow = sign(s.session_key, "flow", {"state": state, "nonce": nonce, "verifier": verifier},
                FLOW_SECONDS)
    return url, flow

def _exchange(s, code, verifier):
    """The ID token's claims, from Google's token endpoint. Replaced in tests."""
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode({
        "code": code, "client_id": s.client_id, "client_secret": s.client_secret,
        "redirect_uri": s.redirect_uri, "grant_type": "authorization_code",
        "code_verifier": verifier}).encode())
    with urllib.request.urlopen(req, timeout=10) as r:
        id_token = json.load(r)["id_token"]
    return json.loads(_unb64(id_token.split(".")[1]))

class SignInError(Exception):
    pass

def finish(s, flow_cookie, state, code):
    """Check the callback and return the email it proves. Raises SignInError otherwise."""
    flow = unsign(s.session_key, "flow", flow_cookie)
    if not flow or not state or not hmac.compare_digest(flow["state"], state):
        raise SignInError("This sign-in link has expired or didn't start here. Try again.")
    claims = _exchange(s, code, flow["verifier"])
    checks = [claims.get("iss") in ISSUERS,
              claims.get("aud") == s.client_id,
              claims.get("exp", 0) > time.time(),
              hmac.compare_digest(str(claims.get("nonce", "")), flow["nonce"]),
              claims.get("email_verified") is True]
    if not all(checks):
        raise SignInError("Google's answer didn't check out. Try again.")
    return claims["email"].lower()

def session_cookie(s, email):
    return sign(s.session_key, "session", {"email": email, "sid": secrets.token_urlsafe(16)},
                SESSION_DAYS * 86400)

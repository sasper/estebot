"""Whose page a request is for.

The page used to read one config at import and serve it to everyone. A Person carries what
differs between people: their config (and so their data directory and database), their goals
and their training split. Every route works out its Person first and uses nothing global.

With sign-in off (web/auth.py) there is only ever one: the config this process was started
with. With it on, a signed-in email gets the config the users file gives it, and everyone
else gets the read-only demo.
"""
from dataclasses import dataclass, replace
from functools import lru_cache

from estebot import config
from estebot.analysis import engine
from estebot.web import auth, db


@dataclass(frozen=True)
class Person:
    cfg: config.Config
    training: engine.Training
    read_only: bool = False      # the demo: shown to anyone, changed by no one
    email: str | None = None     # who signed in, when sign-in is on
    session: dict | None = None

    @property
    def goals(self):
        return self.cfg.goals

    @property
    def db_path(self):
        return str(self.cfg.data_file("app.sqlite"))

    def connect(self):
        return db.connect(self.db_path)


@lru_cache(maxsize=None)
def load(path=None):
    """The Person for a config file (None: this process's own config). Their database gets
    its tables on first use, so a new person's first page load doesn't fail."""
    cfg = config.load(path)
    p = Person(cfg, engine.Training.from_config(cfg.training))
    db.init(p.db_path).close()
    return p


def for_request(request):
    s = auth.settings()
    if s is None:
        return load()
    sess = auth.session(request, s) if request is not None else None
    if sess:
        return replace(load(s.users[sess["email"]]), email=sess["email"], session=sess)
    if not s.demo_config:
        raise RuntimeError("sign-in is on but the users file has no demo_config")
    return replace(load(s.demo_config), read_only=True)

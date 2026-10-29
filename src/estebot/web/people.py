"""Whose page a request is for.

The page used to read one config at import and serve it to everyone. A Person carries what
differs between people: their config (and so their data directory and database), their goals
and their training split. Every route works out its Person first and uses nothing global.

For now there is only ever one: the config this process was started with. Signing in is what
will choose between people.
"""
from dataclasses import dataclass
from functools import lru_cache

from estebot import config
from estebot.analysis import engine
from estebot.web import db


@dataclass(frozen=True)
class Person:
    cfg: config.Config
    training: engine.Training

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
    return load()

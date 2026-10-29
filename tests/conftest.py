import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_people():
    # web.people caches one Person per config path; each test writes its own config.
    # Importing it here would read a config before the test has written one.
    def clear():
        if (people := sys.modules.get("estebot.web.people")) is not None:
            people.load.cache_clear()
    clear()
    yield
    clear()


@pytest.fixture
def con(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    from estebot import config
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    from estebot.web import db as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "DB", str(data / "app.sqlite"))
    from estebot.analysis import engine
    importlib.reload(engine)
    return mod.init()

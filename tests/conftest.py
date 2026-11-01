import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_people():
    # web.people caches one Person per config path; each test writes its own config.
    # Importing it here would read a config before the test has written one.
    def clear():
        if (people := sys.modules.get("estecoach.web.people")) is not None:
            people.load.cache_clear()
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _no_desktop_notifications(monkeypatch):
    # daily._notify shells out to osascript. A test that fakes a failed step would otherwise
    # post a real "hevy failed" banner on the Mac running pytest, pointing at a log that
    # never got the failure.
    from estecoach import daily
    monkeypatch.setattr(daily, "_notify", lambda msg: None)


@pytest.fixture
def con(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estecoach.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    from estecoach import config
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])
    from estecoach.web import db as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "DB", str(data / "app.sqlite"))
    from estecoach.analysis import engine
    importlib.reload(engine)
    return mod.init()

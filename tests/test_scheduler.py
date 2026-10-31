import importlib.util
from datetime import datetime
from pathlib import Path

import pytest


def _scheduler(monkeypatch, at):
    # deploy/ isn't a package: the image copies scheduler.py to /app on its own.
    monkeypatch.setenv("SYNC_AT", at)
    path = Path(__file__).resolve().parents[1] / "deploy" / "scheduler.py"
    spec = importlib.util.spec_from_file_location("scheduler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sync_at_takes_a_list_and_waits_for_the_soonest(monkeypatch):
    # The noon sync alone left yesterday's food and late workouts out of the morning's call.
    # 04:30 brings them in before waking; noon stays as the backstop.
    s = _scheduler(monkeypatch, "04:30, 12:00")
    assert s.AT == ["04:30", "12:00"]
    assert s.seconds_until_next(s.AT, datetime(2026, 10, 31, 1, 0)) == 3.5 * 3600
    assert s.seconds_until_next(s.AT, datetime(2026, 10, 31, 4, 30)) == 7.5 * 3600   # just ran
    assert s.seconds_until_next(s.AT, datetime(2026, 10, 31, 13, 0)) == 15.5 * 3600  # tomorrow 04:30
    assert _scheduler(monkeypatch, "12:00").AT == ["12:00"]


def test_fly_runs_the_sync_before_waking_and_at_noon():
    import tomllib
    fly = tomllib.loads((Path(__file__).resolve().parents[1] / "fly.toml").read_text())
    assert fly["env"]["SYNC_AT"] == "04:30,12:00"


@pytest.mark.parametrize("bad", ["", "4:30,25:00", "noon"])
def test_a_bad_sync_at_fails_at_start_not_at_four_in_the_morning(monkeypatch, bad):
    with pytest.raises(ValueError):
        _scheduler(monkeypatch, bad)

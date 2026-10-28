import importlib
from datetime import date

import pytest

from estebot import demo

END = date(2026, 10, 24)


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_same_seed_writes_the_same_bytes(tmp_path):
    # The hosted demo regenerates on deploy; a page that changed on every deploy with no new
    # code would hide real regressions behind noise.
    out = tmp_path / "demo"
    demo.generate(out, seed=7, end=END, days=60)
    first = _files(out)
    for p in sorted(out.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    demo.generate(out, seed=7, end=END, days=60)
    assert _files(out) == first
    demo.generate(tmp_path / "other", seed=8, end=END, days=60)
    assert _files(tmp_path / "other")["tidy/sets.csv"] != first["tidy/sets.csv"]


def test_it_refuses_a_directory_it_did_not_write(tmp_path):
    # --out pointed at a real data repo by mistake must not overwrite it.
    (tmp_path / "ledger.md").write_text("mine\n")
    with pytest.raises(SystemExit):
        demo.main(["--out", str(tmp_path)])
    assert (tmp_path / "ledger.md").read_text() == "mine\n"


def test_the_app_runs_on_the_demo(tmp_path, monkeypatch):
    out = demo.generate(tmp_path / "demo", seed=7, end=END, days=60)
    from estebot import config
    monkeypatch.setattr(config, "_SEARCH", [str(out / "estebot.toml")])
    from estebot.web import db
    importlib.reload(db)                 # both resolve the data dir at import
    from estebot.web import ingest
    importlib.reload(ingest)
    from estebot.analysis import engine
    importlib.reload(engine)
    con = ingest.run()

    day = END.isoformat()
    r = engine.readiness(con, day, config.load().goals)
    measured = [s["area"] for s in r["signals"] if s["level"]]
    assert len(r["signals"]) == 7 and len(measured) >= 6, r["signals"]

    plan = engine.training_plan(con, day, r["verdict"], r["checkin"])
    assert plan["kind"] in engine.ROTATION and plan["variant"] in ("A", "B")
    assert plan["main"] and len(plan["exercises"]) >= 4

    days = engine.training_days(con, (END.replace(day=1)).isoformat(), day)
    assert {x["kind"] for x in days.values()} == {"training", "movement", "rest"}
    assert engine.unwell_days(con, "2026-08-26", day)    # the sick days and the back

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


def test_the_goal_is_ahead_of_the_trend_and_p001_is_the_planned_pace(tmp_path):
    # The goal was a fixed 175 lb, already passed while the cut went on, and P-001 asked for
    # 1-2 lb a week against a planned 0.5-1.0% of body weight.
    import csv, tomllib
    from estebot import demo
    out = demo.generate(tmp_path / "d")
    cfg = tomllib.loads((out / "estebot.toml").read_text())["goals"]
    last = [float(r["weight_lb"]) for r in csv.DictReader(open(out / "tidy" / "body_mass.csv"))][-14:]
    assert cfg["target_weight_lb"] < sum(last) / len(last) - 3
    lo, hi = tomllib.loads((out / "predictions.toml").read_text())["P-001"]["band"]
    # the band is the planned percentages of one weight, so its ends keep their ratio (to rounding)
    k = cfg["max_loss_pct_per_week"] / cfg["min_loss_pct_per_week"]
    assert hi < 0 and lo == pytest.approx(k * hi, abs=0.15)
    assert "0.5 to 1% of body weight" in (out / "ledger.md").read_text()

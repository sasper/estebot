"""The call for every day of a demo year, against a snapshot taken on main before the call
panel was rewritten (1 Nov 2026). A change that only rewords or re-lays the page must leave
every day's call as it was; a change that is meant to move calls updates the snapshot and
says why in its PR."""
import importlib, json
from datetime import date, timedelta
from pathlib import Path

from estecoach import demo

END = date(2026, 10, 24)
SNAPSHOT = Path(__file__).parent / "fixtures" / "demo_calls.json"


def calls(out):
    """[{day, verdict, score, levels, voting, holds, rest, positive}] over the demo's history.
    The config must already point at `out`'s estecoach.toml."""
    from estecoach import config
    from estecoach.web import db, ingest
    from estecoach.analysis import engine
    for m in (db, ingest, engine):
        importlib.reload(m)
    con = ingest.run()
    goals = config.load().goals
    _, graded = engine._graded(con, goals)
    first = date.fromisoformat(con.execute("SELECT MIN(date) FROM weight").fetchone()[0])
    rows, day = [], first
    while day <= END:
        r = engine.readiness(con, day.isoformat(), goals, graded=graded)
        rows.append({"day": day.isoformat(), "verdict": r["verdict"], "score": r["score"],
                     "levels": {x["area"]: x["level"] for x in r["signals"]},
                     "voting": list(r["voting"]), "holds": r["holds"], "rest": r["forced_rest"],
                     "positive": r["positive"]})
        day += timedelta(days=1)
    return rows


def generate(tmp):
    return demo.generate(Path(tmp) / "demo", seed=7, end=END, days=365)


def test_no_day_s_call_changed(tmp_path, monkeypatch):
    out = generate(tmp_path)
    from estecoach import config
    monkeypatch.setattr(config, "_SEARCH", [str(out / "estecoach.toml")])
    assert calls(out) == json.loads(SNAPSHOT.read_text())


if __name__ == "__main__":        # writes the snapshot: python tests/test_call_history.py <tmpdir>
    import os, sys
    out = generate(sys.argv[1])
    os.environ["ESTECOACH_CONFIG"] = str(out / "estecoach.toml")
    SNAPSHOT.write_text(json.dumps(calls(out), indent=0) + "\n")
    print("wrote", SNAPSHOT)

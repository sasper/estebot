import importlib
import sqlite3

import pytest

# The checkin table shipped without `source` and `needs_review`, which ingest and the
# check-in form both write. Every fresh database failed, and because ingest commits once at
# the end, the crash rolled back the whole import and left the app with no data.

CHECKIN_WRITE = """INSERT INTO checkin
    (date, back, soreness, energy, inter_set_recovery, note, source, needs_review)
    VALUES ('2000-01-02','ok',0,0,NULL,'x','derived',0)"""

# The checkin table exactly as the schema shipped it, before this fix.
OLD_CHECKIN = """CREATE TABLE checkin (
  date TEXT PRIMARY KEY, back TEXT NOT NULL DEFAULT 'ok', soreness INT, energy INT,
  inter_set_recovery INT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(f'data_dir = "{data}"\n')
    from estebot import config
    monkeypatch.setattr(config, "_SEARCH", [str(cfg)])   # read once at import, not per load
    from estebot.web import db as mod
    mod = importlib.reload(mod)          # module resolves its DB path at import
    monkeypatch.setattr(mod, "DB", str(data / "app.sqlite"))
    return mod


def test_fresh_db_accepts_the_columns_ingest_writes(db):
    con = db.init()
    con.execute(CHECKIN_WRITE)
    assert con.execute("SELECT source FROM checkin").fetchone()["source"] == "derived"


def test_old_db_is_migrated_and_keeps_its_rows(db):
    raw = sqlite3.connect(db.DB)
    raw.execute(OLD_CHECKIN)
    raw.execute("INSERT INTO checkin (date, back, note) VALUES ('2000-01-01','ok','x')")
    raw.commit(); raw.close()

    con = db.init()
    old = con.execute("SELECT * FROM checkin WHERE date='2000-01-01'").fetchone()
    assert old["note"] == "x"
    assert old["source"] == "manual"     # pre-existing rows were all typed by hand
    assert old["needs_review"] == 0
    con.execute(CHECKIN_WRITE)


def test_init_is_idempotent(db):
    db.init(); db.init()



@pytest.mark.parametrize("text", ["back is a bit tender after deadlifts",
                                  "back injury, took the day off",
                                  "tweaked my back"])
def test_back_sore_vocabulary(db, text):
    from estebot.web import ingest
    ingest = importlib.reload(ingest)    # needs the fixture's config at import
    assert ingest.BACK_SORE.search(text)


@pytest.mark.parametrize("text, cues", [
    ("not feeling strong today", (True, False)),
    ("not feeling super strong, legs heavy", (True, False)),
    ("not feeling very strong", (True, False)),
    ("wasn't feeling great this morning", (True, False)),
    ("no pain, felt great", (False, True)),
    ("not tired, feeling strong", (False, True)),
    ("poor sleep but pull-ups felt strong", (True, True)),
])
def test_a_negated_strong_note_is_low_energy_not_a_conflict(db, text, cues):
    # "feeling strong" matched inside "not feeling strong", so a plainly tired note read
    # as both low and high and was sent to review as conflicting.
    from estebot.web import ingest
    ingest = importlib.reload(ingest)    # needs the fixture's config at import
    assert ingest.energy_cues(text) == cues

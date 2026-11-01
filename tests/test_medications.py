import json
from pathlib import Path
from types import SimpleNamespace

from estebot.parsers import health_auto_export as H

DOC = json.loads((Path(__file__).parent / "fixtures" / "medications.json").read_text())


def test_a_dose_becomes_one_row_with_its_schedule():
    # Shaped like the app's Medications export (Export Version 2), names and codes made up.
    assert H.parse_medications([DOC]) == [
        ["2026-01-10", "07:41", "Fictozole 20 mg Delayed Release Oral Capsule", "1", "count", "Taken", "1", "1"],
        ["2026-01-10", "19:05", "Examprofen 200 mg Oral Tablet", "1", "count", "Taken", "0", ""]]


def test_a_dose_in_two_files_for_the_same_day_counts_once():
    # The app sends no id; a resent day must not double the doses.
    assert len(H.parse_medications([DOC, DOC])) == 2


def test_older_doses_stay_when_their_file_is_pruned(tmp_path):
    feed = tmp_path / "feed"; feed.mkdir()
    tidy = tmp_path / "tidy"; tidy.mkdir()
    (tidy / "medications.csv").write_text(",".join(H.MED_COLS) + "\n2026-01-05,07:30,Fictozole 20 mg,1,count,Taken,1,1\n")
    (feed / "HealthAutoExport-2026-01-10.json").write_text(json.dumps(DOC))
    cfg = SimpleNamespace(data_dir=tmp_path, tidy=tidy, sources={"health_auto_export": {
        "metrics_glob": "", "medications_glob": str(feed / "HealthAutoExport-*.json"), "from_date": "2026-01-01"}})
    H.apply(cfg)
    days = [line.split(",")[0] for line in (tidy / "medications.csv").read_text().splitlines()[1:]]
    assert days == ["2026-01-05", "2026-01-10", "2026-01-10"]


def test_the_ci_check_and_gitignore_keep_medications_out_of_the_repo():
    root = Path(__file__).parent.parent
    assert "medications.csv" in (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "medications.csv" in (root / ".gitignore").read_text().splitlines()


def test_a_medication_is_a_candidate_input_never_a_voting_area(con):
    from estebot.analysis import engine
    con.execute("INSERT INTO medication VALUES ('2026-01-10','07:41','Fictozole 20 mg',1,'count','Taken',1,1)")
    assert engine.medication_days(con, "2026-01-01", "2026-01-31") == {"Fictozole 20 mg": {"2026-01-10"}}
    assert engine.doses_today(con, "2026-01-11") == [{"name": "Fictozole 20 mg", "logged": None}]
    assert engine.doses_today(con, "2026-01-10")[0]["logged"] == {"time": "07:41", "status": "Taken"}
    ev = engine.evidence(con, "2026-01-01", "2026-01-31", {})
    assert "took Fictozole 20 mg" in ev["candidates"] and "Fictozole 20 mg" not in ev["areas"]


def test_the_chat_reports_doses_and_is_told_never_to_advise_on_them(con):
    from estebot.web import chat
    con.execute("INSERT INTO medication VALUES ('2026-01-10','07:41','Fictozole 20 mg',1,'count','Taken',1,1)")
    out = chat.run_tool(SimpleNamespace(goals={}, training=None), con, "medications",
                        {"start": "2026-01-01", "end": "2026-01-31"})
    assert "Fictozole 20 mg" in json.dumps(out)
    assert "pharmacist" in chat.SYSTEM and "Never give dosing advice" in chat.SYSTEM

def test_the_first_call_of_the_day_is_kept(tmp_path):
    # The call is graded later against the session that followed, so it has to stay as it
    # was made: a later run that day (a phone upload, the noon sync) must not replace it.
    from estebot.analysis import calls
    path = tmp_path / "calls.csv"
    row = {f: "" for f in calls.FIELDS} | {"date": "2026-10-30", "verdict": "normal"}
    assert calls.record(path, row)
    assert not calls.record(path, row | {"verdict": "hard"})
    assert calls.record(path, row | {"date": "2026-10-31", "verdict": "light"})
    assert [(c["date"], c["verdict"]) for c in calls.load(path)] == [
        ("2026-10-30", "normal"), ("2026-10-31", "light")]


def test_a_morning_call_waits_for_sleep_and_the_check_in_until_noon(con):
    from datetime import datetime
    from estebot.analysis import calls
    morning = datetime(2026, 10, 30, 7, 0)
    assert calls.waiting_for(con, morning) == ["last night's sleep", "this morning's check-in"]
    con.execute("INSERT INTO sleep (night, asleep_hr) VALUES ('2026-10-29', 7.1)")
    assert calls.waiting_for(con, morning) == ["this morning's check-in"]
    con.execute("INSERT INTO checkin (date, back, energy, source, back_reported) "
                "VALUES ('2026-10-30', 'ok', 2, 'manual', 1)")
    assert calls.waiting_for(con, morning) == []
    con.execute("DELETE FROM checkin")
    assert calls.waiting_for(con, datetime(2026, 10, 30, 12, 0)) == []   # noon: record what there is


def test_an_upload_after_the_days_call_doesnt_make_another(con, monkeypatch, capsys):
    # Every upload's sync ends with this step, and making a call grades every session (~10 s
    # on the server); only the day's first is kept, so the rest were thrown away.
    from datetime import date
    from estebot import config
    from estebot.analysis import calls
    path = config.load().data_file("calls.csv")
    calls.record(path, {"date": date.today().isoformat(), "verdict": "normal"})
    monkeypatch.setattr(calls, "make_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made")))
    assert calls.main([]) == 0
    assert "already recorded today" in capsys.readouterr().out

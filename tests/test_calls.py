def test_the_first_call_of_the_day_is_kept(tmp_path):
    # The call is graded later against the session that followed, so it has to stay as it
    # was made: a later run that day (a phone upload, the noon sync) must not replace it.
    from estecoach.analysis import calls
    path = tmp_path / "calls.csv"
    row = {f: "" for f in calls.FIELDS} | {"date": "2026-10-30", "verdict": "normal"}
    assert calls.record(path, row)
    assert not calls.record(path, row | {"verdict": "hard"})
    assert calls.record(path, row | {"date": "2026-10-31", "verdict": "light"})
    assert [(c["date"], c["verdict"]) for c in calls.load(path)] == [
        ("2026-10-30", "normal"), ("2026-10-31", "light")]


def test_a_morning_call_waits_for_sleep_and_the_check_in_until_noon(con):
    from datetime import datetime
    from estecoach.analysis import calls
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
    from estecoach import config
    from estecoach.analysis import calls
    path = config.load().data_file("calls.csv")
    calls.record(path, {"date": date.today().isoformat(), "verdict": "normal"})
    monkeypatch.setattr(calls, "make_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made")))
    assert calls.main([]) == 0
    assert "already recorded today" in capsys.readouterr().out


def _resave_setup(con, monkeypatch, saved_at):
    from estecoach import config
    from estecoach.analysis import calls
    path = config.load().data_file("calls.csv")
    kept = {f: "" for f in calls.FIELDS} | {"date": "2026-11-04", "recorded_at": "2026-11-04T08:02:26",
                                            "verdict": "normal", "score": "71", "plan": "push B",
                                            "targets": '[{"action":"deload"}]'}
    calls.record(path, kept)
    con.execute("INSERT INTO checkin (date, back, energy, source, back_reported, saved_at) "
                "VALUES ('2026-11-04', 'ok', 3, 'manual', 1, ?)", (saved_at,))
    con.commit()
    sent = []
    monkeypatch.setattr(calls, "_prefill_hevy", lambda cfg, plan, verdict, again=False: sent.append(again))
    return calls, path, kept, sent


def test_a_check_in_saved_again_before_the_session_remakes_the_call(con, monkeypatch, tmp_path):
    # A mis-tapped back level fixed 12 s later left a whole session at 60%: the first save
    # recorded the call and the second was turned away.
    calls, path, kept, sent = _resave_setup(con, monkeypatch, "2026-11-04T08:02:38")
    assert calls.resaved(con, kept)
    new = dict(kept, recorded_at="2026-11-04T08:02:40", targets='[{"action":"repeat"}]')
    monkeypatch.setattr(calls, "call_and_plan", lambda *a, **k: (new, {"kind": "push", "variant": "B"}))
    monkeypatch.setattr(calls, "datetime", type("D", (), {"now": staticmethod(lambda: __import__("datetime").datetime(2026, 11, 4, 8, 3))}))
    assert calls._remake(calls.config.load(), con, path, kept) == 0
    assert [c["targets"] for c in calls.load(path)] == ['[{"action":"repeat"}]']
    gone = calls.load(path.with_name(calls.REPLACED))
    assert [(c["targets"], c["reason"]) for c in gone] == [('[{"action":"deload"}]', calls.RESAVED)]
    assert con.execute("SELECT targets FROM call_record WHERE date='2026-11-04'").fetchone()[0] == '[{"action":"repeat"}]'
    assert sent == [True]


def test_an_unchanged_call_after_a_re_save_is_left_alone(con, monkeypatch):
    calls, path, kept, sent = _resave_setup(con, monkeypatch, "2026-11-04T08:02:38")
    monkeypatch.setattr(calls, "call_and_plan", lambda *a, **k: (dict(kept, recorded_at="x"), None))
    assert calls._remake(calls.config.load(), con, path, kept) == 0
    assert not path.with_name(calls.REPLACED).exists() and sent == []


def test_the_call_locks_once_lifting_is_logged_or_the_save_came_first(con, monkeypatch):
    calls, path, kept, sent = _resave_setup(con, monkeypatch, "2026-11-04T08:02:22")
    assert not calls.resaved(con, kept)                       # the save the call was made from
    con.execute("UPDATE checkin SET saved_at='2026-11-04T09:30:00'")
    assert calls.resaved(con, kept)
    con.execute("INSERT INTO lift_set (date, exercise, set_idx, weight_lb, reps) "
                "VALUES ('2026-11-04', 'Barbell Military Press', 1, 205, 3)")
    assert not calls.resaved(con, kept)                       # the session has started

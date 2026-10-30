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

def test_the_load_chart_shows_the_ratio_the_verdict_uses(con):
    # The page draws the readiness inputs next to the verdict; a chart that computed its
    # own ratio could contradict the verdict printed above it.
    from datetime import date, timedelta
    from estebot.analysis import engine
    day0 = date(2026, 9, 1)
    rows = [((day0 + timedelta(days=i)).isoformat(), 30.0 if i < 30 else 150.0) for i in range(40)]
    con.executemany("INSERT INTO daily (date, exercise_min) VALUES (?, ?)", rows)
    series = dict(engine.recovery_series(con, "2026-10-01", "2026-10-10")["acwr"])
    for dt, v in series.items():
        assert v == engine.acwr(con, dt.isoformat())
    assert series[date(2026, 10, 10)] > 1.5     # the spike shows up past the threshold line


def test_the_weight_rate_uses_four_calendar_weeks_not_the_last_28_weigh_ins(con):
    # The page used w[-28:]: with sparse weigh-ins (gaps of weeks to months) that reached
    # back months, so a "4-week trend" could describe the spring.
    from estebot.analysis import engine
    old = [("2026-05-01", 230.0), ("2026-06-01", 220.0), ("2026-07-01", 210.0)]
    new = [("2026-09-20", 200.0), ("2026-10-01", 200.0), ("2026-10-10", 200.0)]
    con.executemany("INSERT INTO weight (date, weight_lb) VALUES (?, ?)", old + new)
    ws = engine.weight_status(con, "2026-10-10", 180.0, 1.0)
    assert ws["n"] == 3 and ws["rate"] == 0.0      # flat for the last four weeks
    assert ws["eta"] is None                       # so no date is promised


def test_the_three_load_charts_share_one_axis(con):
    # bar_chart places bars by index, so a series that skipped rest days would put
    # Wednesday's sets under Thursday's minutes. Every day must be present in all three.
    from estebot.analysis import engine
    con.execute("INSERT INTO daily (date, exercise_min) VALUES ('2026-10-01', 90)")
    con.execute("INSERT INTO lift_session (date, n_sets, total_volume_lb, hard_sets) "
                "VALUES ('2026-10-03', 20, 18000, 17)")
    tl = engine.training_load_series(con, "2026-10-01", "2026-10-04")
    assert [len(v) for v in tl.values()] == [4, 4, 4]
    assert [v for _, v in tl["hard_sets"]] == [0, 0, 17, 0]
    assert tl["tonnage_klb"][2][1] == 18.0


def test_a_non_lifting_workout_is_training_and_a_walk_alone_is_rest(con):
    # Rest Sunday, lift Monday, an "Other" workout + walks Tuesday, lift Wed and Thu.
    # Lifting-only counting said "1 day streak" because Tuesday broke it. It is 4.
    from estebot.analysis import engine
    for d_, n in (("2026-10-12", 17), ("2026-10-14", 27), ("2026-10-15", 17)):
        con.execute("INSERT INTO lift_session (date, n_sets) VALUES (?, ?)", (d_, n))
    con.executemany("INSERT INTO workout VALUES (?,?,?,?)", [
        ("2026-10-11", "17:00", "Walking", 20.0),        # Sunday: a walk is still rest
        ("2026-10-13", "09:29", "Other", 72.1),          # not lifting, still training
        ("2026-10-13", "11:02", "Walking", 15.1)])
    assert engine.days_since_rest(con, "2026-10-15") == 4
    assert engine.days_since_rest(con, "2026-10-14") == 3
    assert engine.last_rest_day(con, "2026-10-15") == "2026-10-11"


def test_a_thin_week_is_not_judged(con):
    # One week swings 2-3 lb on water; with fewer than 4 weigh-ins it says nothing.
    from estebot.analysis import engine
    con.executemany("INSERT INTO weight (date, weight_lb) VALUES (?, ?)",
                    [("2026-10-05", 199.0), ("2026-10-12", 198.0), ("2026-10-15", 197.0)])
    wc = engine.weight_changes(con, "2026-10-15", 0.5, 1.0)
    assert wc["wow"]["status"] is None and wc["mom"]["status"] is None


def test_the_calendar_colours_days_by_the_rest_day_rule(con):
    # The calendar and the 'days since rest' tile must agree on every day.
    from estebot.analysis import engine
    con.execute("INSERT INTO lift_session (date, n_sets, hard_sets) VALUES ('2026-10-12', 17, 17)")
    con.executemany("INSERT INTO workout VALUES (?,?,?,?)", [
        ("2026-10-11", "17:00", "Walking", 20.0), ("2026-10-13", "09:29", "Other", 72.1)])
    cal = engine.training_days(con, "2026-10-11", "2026-10-13")
    assert {k: v["training"] for k, v in cal.items()} == {
        "2026-10-11": False, "2026-10-12": True, "2026-10-13": True}
    assert cal["2026-10-13"]["workouts"] == [("Other", 72.1)]
    assert cal["2026-10-12"]["lift"]["hard_sets"] == 17


def test_a_walk_alone_is_rest_day_movement_not_plain_rest(con):
    # A long treadmill walk on its own. The calendar filed walk-only days as plain rest,
    # hiding cardio done on purpose; it must still not count as training.
    from estebot.analysis import engine
    con.execute("INSERT INTO lift_session (date, n_sets) VALUES ('2026-10-27', 18)")
    con.executemany("INSERT INTO workout VALUES (?,?,?,?)", [
        ("2026-10-26", "08:48", "Walking", 46.2), ("2026-10-27", "11:33", "Walking", 14.3)])
    cal = engine.training_days(con, "2026-10-25", "2026-10-27")
    assert {k: v["kind"] for k, v in cal.items()} == {
        "2026-10-25": "rest", "2026-10-26": "movement", "2026-10-27": "training"}
    assert cal["2026-10-26"]["training"] is False
    assert engine.days_since_rest(con, "2026-10-27") == 1


def test_the_page_opens_on_the_last_day_with_data(con):
    # The page opened on max(today, latest weigh-in), so a data set that ends before today
    # opened on an empty day with no verdict inputs at all.
    from estebot.analysis import engine
    assert engine.default_day(con, "2026-10-28") == "2026-10-28"          # nothing yet
    con.execute("INSERT INTO weight (date, weight_lb) VALUES ('2026-10-20', 200)")
    con.execute("INSERT INTO sleep (night, asleep_hr) VALUES ('2026-10-23', 7)")
    assert engine.default_day(con, "2026-10-28") == "2026-10-23"
    # yesterday counts as current: the morning before the day's sync is still today
    con.execute("INSERT INTO daily (date, steps) VALUES ('2026-10-27', 5000)")
    assert engine.default_day(con, "2026-10-28") == "2026-10-28"
    # a row dated after the day asked about is not evidence about it
    assert engine.default_day(con, "2026-10-21") == "2026-10-21"


def test_spinal_load_ranking_reads_a_trailing_window(con):
    # The ranking started at a fixed date, so a history that ended before it got none and
    # a sore back got no day-type suggestion.
    from estebot.analysis import engine
    for day, groups, ex in (("2025-03-03", "chest|triceps", "Machine Chest Press"),
                            ("2025-03-04", "back|hamstrings", "Barbell Deadlift"),
                            ("2024-06-01", "quads", "Barbell Squat")):
        con.execute("INSERT INTO lift_session (date, muscle_groups) VALUES (?, ?)", (day, groups))
        con.execute("INSERT INTO e1rm (exercise, date) VALUES (?, ?)", (ex, day))
    ranked = engine.axial_by_day_type(con, "2025-03-10")
    assert list(ranked) == ["push", "pull"]          # the old squat day is out of the window
    con.execute("INSERT INTO checkin (date, back, source, back_reported) VALUES ('2025-03-10', 'sore', 'derived', 1)")
    assert engine.recommend_pattern(con, "2025-03-10", engine.latest_checkin(con, "2025-03-10"))[0] == "push"


# The goals estebot.toml carries; readiness needs them so the CLI, the page and the tests
# judge protein and pace the same way.
GOALS = {"protein_g_per_lb_target": 0.65, "min_loss_pct_per_week": 0.5,
         "max_loss_pct_per_week": 1.0, "target_weight_lb": 180}


def _minutes(con, start, values):
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    con.executemany("INSERT OR REPLACE INTO daily (date, exercise_min) VALUES (?, ?)",
                    [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)])


def test_coming_off_a_layoff_is_never_go_all_out(con):
    # A big session straight off a near-idle week preceded an injury the next day. A load
    # ratio around 0.5 must not read as "fine", and the ramp cap must fire.
    from estebot.analysis import engine
    _minutes(con, "2026-03-01", [75] * 27 + [10] * 5)
    r = engine.readiness(con, "2026-04-02", GOALS)
    assert r["verdict"] != "hard" and r["ramp"]
    assert next(x for x in r["signals"] if x["area"] == "load")["level"] == "amber"


def test_six_days_in_a_row_alone_does_not_force_a_rest_day(con):
    # The old rule: 6 consecutive training days -> rest, whatever else said. Now it is
    # information; with varied load and nothing else flagged the day is not rest.
    from estebot.analysis import engine
    _minutes(con, "2026-09-10", [70, 20, 90, 60, 0, 80, 40] * 4 + [100, 60, 130, 70, 90, 50])
    con.executemany("INSERT INTO lift_session (date, n_sets) VALUES (?, 20)",
                    [(f"2026-10-{i:02d}", ) for i in range(8, 14)])
    r = engine.readiness(con, "2026-10-14", GOALS)
    assert r["streak"] == 6 and r["verdict"] != "rest"


def _calm_week(con):
    # varied, ordinary load with nothing else flagged
    _minutes(con, "2026-09-10", [70, 20, 90, 60, 0, 80, 40] * 5)


def test_a_back_spasm_forces_rest_on_its_own(con):
    # The one deliberate exception to "no single input decides": with everything else
    # unremarkable, a spasm alone used to read "take it easy" while the pattern suggestion
    # beside it said "no session".
    from estebot.analysis import engine
    _calm_week(con)
    con.execute("INSERT INTO checkin (date, back, source, back_reported) VALUES ('2026-10-14', 'spasm', 'derived', 1)")
    r = engine.readiness(con, "2026-10-14", GOALS)
    assert r["verdict"] == "rest"
    assert engine.recommend_pattern(con, "2026-10-14", r["checkin"])[0] == "rest"


def test_a_note_that_never_mentions_the_back_is_not_a_green_back(con):
    # checkin.back defaults to 'ok', so an energy-only note used to show "back ok today".
    from estebot.analysis import engine
    _calm_week(con)
    con.execute("INSERT INTO checkin (date, back, energy, source, back_reported) "
                "VALUES ('2026-10-14', 'ok', 3, 'derived', 0)")
    feel = next(x for x in engine.readiness(con, "2026-10-14", GOALS)["signals"] if x["area"] == "feel")
    assert not any("back" in reason for reason in feel["reasons"])


def test_missing_nutrition_values_are_skipped_not_zero(con):
    # A complete day with no protein value read as 0 g (turning nutrition red), and one with
    # no kcal crashed the page.
    from estebot.analysis import engine
    _calm_week(con)
    con.executemany("INSERT INTO nutrition (date, kcal, protein_g, complete) VALUES (?,?,?,1)", [
        ("2026-10-11", 2500, 180), ("2026-10-12", None, 175), ("2026-10-13", 2600, None)])
    nut = next(x for x in engine.readiness(con, "2026-10-14", GOALS)["signals"] if x["area"] == "nutrition")
    assert nut["level"] != "red"


def test_strength_standards_use_the_last_eight_weeks_and_the_goal_weight(con):
    from estebot.analysis import engine
    con.executemany("INSERT INTO e1rm (exercise, date, best_e1rm_lb) VALUES (?, ?, ?)",
                    [("Barbell Squat", "2026-06-01", 550.0),     # outside the 8 weeks
                     ("Barbell Squat", "2026-09-01", 450.0),
                     ("Barbell Bench Press", "2026-10-01", 300.0),
                     ("Barbell Deadlift", "2026-10-10", 500.0)])
    out = engine.strength_standards(con, "2026-10-19", 250.0, 200.0)
    rows = {r["lift"]: r for r in out["rows"]}
    assert rows["squat"]["e1rm"] == 450.0
    assert rows["press"]["e1rm"] is None and "level" not in rows["press"]
    assert out["total"] == 1250.0 and out["goal_dots"] > out["dots"]
    assert rows["squat"]["goal_level"] == "advanced"


def _session(con, day, groups, sets):
    con.execute("INSERT INTO lift_session (date, n_sets, muscle_groups) VALUES (?,?,?)",
                (day, len(sets), groups))
    con.executemany("INSERT INTO lift_set VALUES (?,?,?,?,?,?,NULL)",
                    [(day, ex, i, w, r, rpe) for i, (ex, w, r, rpe) in enumerate(sets)])


def test_the_plan_follows_the_rotation_and_alternates_the_version(con):
    # 20 Oct push B (press), 25 Oct push A (bench), 26 Oct pull, 27 Oct legs: 28 Oct is
    # push B, built from 20 Oct's exercises, with each target from that exercise's last day.
    from estebot.analysis import engine
    push = "chest|shoulders|triceps"
    _session(con, "2026-10-20", push, [("Barbell Military Press", 185, 3, 10)] * 3
             + [("Dumbbell Lateral Raise", 25, 10, 8)] * 2)
    _session(con, "2026-10-25", push, [("Barbell Bench Press", 225, 4, 8.5)] * 3
             + [("Dumbbell Lateral Raise", 25, 8, 8)] * 2)
    _session(con, "2026-10-26", "back|biceps|olympic", [("Barbell Deadlift", 315, 6, 7.5)] * 5)
    _session(con, "2026-10-27", "quads|hamstrings|calves", [("Barbell Front Squat", 225, 2, 9.5)] * 5)
    p = engine.training_plan(con, "2026-10-28", "normal")
    assert (p["kind"], p["variant"], p["template"]) == ("push", "B", "2026-10-20")
    assert [x["exercise"] for x in p["exercises"]] == ["Barbell Military Press", "Dumbbell Lateral Raise"]
    assert p["exercises"][1]["last_date"] == "2026-10-25"       # the latest, from push A
    assert p["exercises"][1]["target"] == "25 × 9"


def test_a_take_it_easy_day_holds_and_a_sore_back_drops_spinal_lifts(con):
    from estebot.analysis import engine
    _session(con, "2026-10-21", "back|biceps|olympic", [("Barbell Hang Clean", 155, 3, 8)] * 3
             + [("Dumbbell Incline Curl", 30, 8, 8)] * 3)
    _session(con, "2026-10-22", "quads|hamstrings|calves", [("Barbell Squat", 315, 1, 9)] * 5)
    _session(con, "2026-10-25", "chest|shoulders|triceps", [("Barbell Bench Press", 225, 4, 8.5)] * 5)
    light = engine.training_plan(con, "2026-10-26", "light")
    assert "Take it easy" in light["day_adjust"]                 # said once, for the session
    assert not any(x.get("adjust") for x in light["exercises"])
    con.execute("INSERT INTO checkin (date, back, back_reported) VALUES ('2026-10-26', 'sore', 1)")
    ci = con.execute("SELECT * FROM checkin WHERE date='2026-10-26'").fetchone()
    sore = {x["exercise"]: x.get("adjust") for x in engine.training_plan(con, "2026-10-26", "normal", ci)["exercises"]}
    assert sore["Barbell Hang Clean"] == "Back sore: skip it today."
    assert sore["Dumbbell Incline Curl"] is None


UPPER_LOWER = """
[training]
rotation = ["upper", "lower"]
rep_range = [6, 10]
[training.day_types]
upper = ["chest", "back", "shoulders"]
lower = ["quads", "hamstrings"]
[training.variants.upper]
A = ["Barbell Bench Press"]
B = ["Barbell Military Press"]
[training.variants.lower]
A = ["Barbell Squat"]
B = ["Barbell Deadlift"]
"""

def _upper_lower_week(con):
    _session(con, "2026-10-20", "quads|hamstrings", [("Barbell Deadlift", 315, 5, 8)] * 5)
    _session(con, "2026-10-22", "chest|back|shoulders", [("Barbell Bench Press", 185, 5, 8)] * 3
             + [("Dumbbell Row", 60, 10, 8)] * 3)
    _session(con, "2026-10-24", "quads|hamstrings", [("Barbell Squat", 225, 5, 8)] * 5)


def test_a_second_person_on_an_upper_lower_split_gets_their_own_plan(con, tmp_path):
    # The rotation, its versions and the rep ranges were one person's, hard-coded. Someone
    # who trains upper/lower got "push" next and a row target from the wrong rep range.
    import importlib
    from estebot.analysis import engine
    cfg = tmp_path / "estebot.toml"
    cfg.write_text(cfg.read_text() + UPPER_LOWER)
    engine = importlib.reload(engine)
    _upper_lower_week(con)
    p = engine.training_plan(con, "2026-10-26", "normal")
    assert (p["kind"], p["variant"], p["template"]) == ("upper", "B", "2026-10-22")
    assert {x["exercise"]: x["target"] for x in p["exercises"]}["Dumbbell Row"] == "65 × 6"


def test_without_a_training_section_the_plan_keeps_push_pull_legs(con):
    from estebot.analysis import engine
    assert engine.TRAINING == engine.Training.from_config({})
    assert engine.TRAINING.rotation == ("push", "pull", "legs")
    _upper_lower_week(con)
    p = engine.training_plan(con, "2026-10-26", "normal")
    assert (p["kind"], p["variant"]) == ("push", "B")
    assert {x["exercise"]: x["target"] for x in p["exercises"]}["Dumbbell Row"] == "60 × 11"


def test_sick_and_injured_days_come_from_narrow_words(con):
    # The calendar rings these. The ingest's excuse words match "ill" inside "will" and
    # "still"; neither is being unwell, and nor is "cold" in "cold brew".
    from estebot.analysis import engine
    con.executemany("INSERT INTO annotation (start_date, end_date, type, note, source) VALUES (?,?,?,?,?)", [
        ("2026-09-08", "2026-09-08", "injury", "hurt moving furniture", "a"),
        ("2026-09-12", "2026-09-15", "context", "knee injury, out of the gym", "b"),
        ("2026-10-01", None, "context", "still on plan, will deload next week", "b"),
        ("2026-10-20", None, "context", "had a cold brew before lifting", "b"),
        ("2026-10-28", None, "context", "migraine, skipped the gym", "b")])
    out = engine.unwell_days(con, "2026-09-01", "2026-10-31")
    assert sorted(out) == ["2026-09-08", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15", "2026-10-28"]
    assert out["2026-09-08"]["injured"] and not out["2026-09-08"]["sick"]
    assert out["2026-10-28"]["sick"] and not out["2026-10-28"]["injured"]


def test_a_sick_note_lowers_energy_and_the_verdict(con):
    # The note rules knew tired and strong but not sick, so "stomach bug, sick all day"
    # read as "nothing in your notes" and the day could still say go all out. The sick
    # words are the calendar's, so "will" and "still" are not being ill.
    import importlib
    from estebot.analysis import engine
    from estebot.web import ingest
    ingest = importlib.reload(ingest)    # needs the fixture's config at import
    _calm_week(con)
    con.executemany("INSERT INTO annotation (start_date, type, note, source) VALUES (?,?,?,?)", [
        ("2026-10-12", "context", "still sore, will rest tomorrow", "health-context.txt"),
        ("2026-10-14", "context", "stomach bug, sick all day", "health-context.txt")])
    ingest.parse_subjective(con)
    assert con.execute("SELECT energy FROM checkin WHERE date='2026-10-14'").fetchone()["energy"] == 0
    assert con.execute("SELECT 1 FROM checkin WHERE date='2026-10-12'").fetchone() is None
    r = engine.readiness(con, "2026-10-14", GOALS)
    assert next(x for x in r["signals"] if x["area"] == "feel")["level"] == "red"
    assert r["verdict"] != "hard"


def test_a_sick_or_injured_check_in_rings_the_calendar_without_a_note(con):
    # Someone who only taps never writes the note the ring used to need.
    from estebot.analysis import engine
    con.executemany("INSERT INTO checkin (date, sick, injured, note) VALUES (?,?,?,?)", [
        ("2026-10-20", 1, 0, None), ("2026-10-21", 0, 1, "rolled an ankle"), ("2026-10-22", 0, 0, None)])
    con.execute("INSERT INTO annotation (start_date, type, note, source) VALUES "
                "('2026-10-21', 'context', 'rolled an ankle, injury', 'b')")
    out = engine.unwell_days(con, "2026-10-01", "2026-10-31")
    assert sorted(out) == ["2026-10-20", "2026-10-21"]
    assert out["2026-10-20"] == {"sick": True, "injured": False, "notes": []}
    assert out["2026-10-21"]["injured"] and len(out["2026-10-21"]["notes"]) == 2


def test_a_session_is_graded_against_the_target_its_last_session_set(con):
    # The outcome the readiness call will be judged by: each exercise against the goal
    # core.progression set from the time before. Warm-ups are not working sets.
    from estebot.analysis import engine
    rows = [("2026-10-01", "Barbell Squat", 1, 315, 5, 8.0, "normal"),
            ("2026-10-01", "Dumbbell Row", 1, 80, 10, 8.0, "normal"),
            ("2026-10-05", "Barbell Squat", 1, 335, 5, None, "warmup"),   # would pass if counted
            ("2026-10-05", "Barbell Squat", 2, 320, 5, 9.0, "normal"),
            ("2026-10-05", "Dumbbell Row", 1, 80, 11, 8.5, "normal"),
            ("2026-10-05", "Cable Fly", 1, 40, 12, 8.0, "normal")]      # first time: no target
    con.executemany("INSERT INTO lift_set (date, exercise, set_idx, weight_lb, reps, rpe, set_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    o = engine.session_outcome(con, "2026-10-05")
    met = {x["exercise"]: x["met"] for x in o["exercises"]}
    assert met == {"Barbell Squat": False, "Dumbbell Row": True}   # squat asked 325 x 5; row 80 x 11
    assert o["hit"] == 0.5
    assert engine.session_outcome(con, "2026-10-02") is None


def test_the_evidence_pairs_each_session_with_the_one_before(con):
    # The after-a-miss candidate reads the previous session's grade; with every session a
    # miss, each one after the first is on the flagged side and none on the clear side.
    from datetime import date, timedelta
    from estebot.analysis import engine
    days = [(date(2026, 9, 1) + timedelta(days=2 * i)).isoformat() for i in range(6)]
    con.executemany("INSERT INTO lift_session (date, n_sets) VALUES (?, 5)", [(x,) for x in days])
    con.executemany("INSERT INTO lift_set (date, exercise, set_idx, weight_lb, reps, rpe, set_type) "
                    "VALUES (?, 'Barbell Squat', 1, 300, 5, 9, 'normal')", [(x,) for x in days])
    ev = engine.evidence(con, days[0], days[-1], {})
    assert ev["sessions"] == 5                       # the first has nothing to be graded against
    c = ev["candidates"]["missed most targets last session"]
    assert (c["n_flagged"], c["n_clear"]) == (4, 0) and c["status"] == "not enough data"


def test_the_evidence_tallied_from_sessions_graded_once_matches_grading_afresh(con):
    # The demo backfill regraded every session once per backfilled day (24 s; it took the
    # site down on a throttled CPU). It now grades them once and tallies each day's window;
    # a window's tally must not depend on sessions outside it.
    from datetime import date, timedelta
    from estebot.analysis import engine
    days = [(date(2026, 9, 1) + timedelta(days=2 * i)).isoformat() for i in range(8)]
    con.executemany("INSERT INTO lift_session (date, n_sets) VALUES (?, 5)", [(x,) for x in days])
    con.executemany("INSERT INTO lift_set (date, exercise, set_idx, weight_lb, reps, rpe, set_type) "
                    "VALUES (?, 'Barbell Squat', 1, ?, 5, 8, 'normal')",
                    [(x, 300 + (10 if i % 3 else -10) * i) for i, x in enumerate(days)])
    graded = engine.graded_sessions(con, days[0], days[-1], {})
    for end in days[2:]:
        assert engine.evidence(con, days[0], end, {}, graded=graded) == engine.evidence(con, days[0], end, {})

def test_sessions_are_graded_once_per_database_change_not_per_day(con):
    # Changing the day on the page regraded every session (~10 s on the server); the grades
    # don't depend on the day, so they're kept until the database file changes.
    import os
    from estebot.analysis import engine
    con.execute("INSERT INTO lift_session (date, n_sets) VALUES ('2026-09-01', 5)")
    con.execute("INSERT INTO lift_set (date, exercise, set_idx, weight_lb, reps, rpe, set_type) "
                "VALUES ('2026-09-01', 'Barbell Squat', 1, 300, 5, 8, 'normal')")
    con.commit()
    first = engine.graded_for(con, {})
    assert engine.graded_for(con, {}) is first and engine.stale_graded() == []
    path = con.execute("PRAGMA database_list").fetchone()[2]
    os.utime(path, (0, os.path.getmtime(path) + 5))           # the sync wrote to it
    assert [p for p, _ in engine.stale_graded()] == [path]
    assert engine.graded_for(con, {}) is not first and engine.stale_graded() == []

def test_a_good_check_in_earns_going_hard_only_once_it_has_a_record(con):
    # "Go all out" came too easily: on a day with nothing flagged it was the default. A good
    # check-in is a reason to go hard only when days with one have gone better, and with no
    # history behind it the call stays as planned.
    from estebot.analysis import engine
    con.execute("INSERT INTO checkin (date, back, energy, source, back_reported) "
                "VALUES ('2026-10-14', 'ok', 3, 'manual', 1)")
    r = engine.readiness(con, "2026-10-14", GOALS)
    assert r["hard_reasons"]["check-in"][0] and r["positive"] is None
    assert r["verdict"] == "normal"


def test_a_voting_area_holds_the_day_and_a_non_voting_one_does_not(con):
    from estebot.analysis import engine
    con.execute("INSERT INTO sleep (night, asleep_hr) VALUES ('2026-10-13', 4.5)")   # red
    assert engine.readiness(con, "2026-10-14", GOALS, voting=()).get("verdict") == "normal"
    assert engine.readiness(con, "2026-10-14", GOALS, voting=("sleep",))["verdict"] == "light"


def test_today_with_nothing_logged_yet_is_not_a_rest_day(con):
    # The demo said "Go hard" with a Legs A session while the calendar filed today as a rest
    # day and 'days since rest' read 0, rest today: the session just hadn't happened yet.
    from datetime import date, timedelta
    from estebot.analysis import engine
    today = date.today()
    days = [(today - timedelta(days=k)).isoformat() for k in (4, 3, 2, 1, 0)]
    for d_ in days[1:4]:                                  # rest, then three training days
        con.execute("INSERT INTO lift_session (date, n_sets) VALUES (?, 18)", (d_,))
    cal = engine.training_days(con, days[0], days[-1])
    assert cal[days[0]]["kind"] == "rest" and cal[days[-1]]["kind"] == "open"
    assert cal[days[-1]]["training"] is False
    r = engine.readiness(con, days[-1], {})
    assert r["today_open"] and r["streak_now"] == 3 and r["last_rest"] == days[0]
    # once today's session is in, it counts
    con.execute("INSERT INTO lift_session (date, n_sets) VALUES (?, 18)", (days[-1],))
    r = engine.readiness(con, days[-1], {})
    assert not r["today_open"] and r["streak_now"] == 4
    # and a past day with nothing logged is a rest day, as before
    assert engine.training_days(con, days[0], days[0], today=days[-1])[days[0]]["kind"] == "rest"

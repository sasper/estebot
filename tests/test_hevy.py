from datetime import timedelta, timezone

from estebot.parsers.hevy import hevy_sets, kg_to_lb, local_date

EST = timezone(timedelta(hours=-5))


def workout(start, end, *exercises):
    return {"start_time": start, "end_time": end,
            "exercises": [{"index": i, "title": t, "sets": [
                {"index": j, "type": typ, "weight_kg": kg, "reps": reps, "rpe": rpe}
                for j, (typ, kg, reps, rpe) in enumerate(sets)]}
                for i, (t, sets) in enumerate(exercises)]}


def test_kg_round_trips_to_the_pounds_that_were_typed():
    # Hevy stores typed pounds as kg floats; the export showed 135, 68.5 and 225.
    for lb in (135.0, 68.5, 225.0, 405.0, 57.5):
        assert kg_to_lb(lb * 0.45359237) == lb
    assert kg_to_lb(None) == 0.0          # bodyweight exercises carry no weight


def test_evening_session_keeps_its_local_date():
    # The API returns UTC. 21:30 local is 02:30 UTC the next day.
    assert local_date("2026-03-03T02:30:00+00:00", EST) == "2026-03-02"
    assert local_date("2026-03-03T14:00:00Z", EST) == "2026-03-03"


def test_same_exercise_in_two_blocks_numbers_sets_continuously():
    # Pull-ups done straight and again inside a superset are one exercise that day.
    w = workout("2026-03-02T14:00:00Z", "2026-03-02T15:00:00Z",
                ("Pull Up", [("normal", None, 6, 7)] * 2),
                ("Hang Clean", [("normal", 100.0, 5, 8)]),
                ("Pull Up", [("normal", None, 6, 8)]))
    rows, unmapped, minutes = hevy_sets([w], {"Pull Up": "Pull-Up"}, EST)
    assert [r["set_idx"] for r in rows if r["exercise"] == "Pull-Up"] == [1, 2, 3]
    assert minutes == {"2026-03-02": 60.0}


def test_unmapped_title_is_kept_as_its_own_exercise_and_reported():
    w = workout("2026-03-02T14:00:00Z", None, ("Mystery Lift", [("normal", 50.0, 5, None)]))
    rows, unmapped, _ = hevy_sets([w], {}, EST)
    assert rows[0]["exercise"] == "Mystery Lift"
    assert unmapped == {"Mystery Lift"}


def test_set_type_and_rpe_are_carried():
    w = workout("2026-03-02T14:00:00Z", None,
                ("Deadlift (Barbell)", [("warmup", 70.0, 10, 6), ("failure", 210.0, 1, 10)]))
    rows, _, _ = hevy_sets([w], {"Deadlift (Barbell)": "Barbell Deadlift"}, EST)
    assert [(r["set_type"], r["rpe"]) for r in rows] == [("warmup", 6), ("failure", 10)]


def test_a_carry_keeps_its_distance_in_the_reps_column():
    w = {"start_time": "2000-01-01T15:00:00Z", "title": "t", "exercises": [
        {"index": 0, "title": "Farmers Walk", "sets": [
            {"index": 0, "weight_kg": 50.0, "reps": None, "distance_meters": 30, "type": "normal"}]}]}
    rows, _, _ = hevy_sets([w], {})
    assert rows[0]["reps"] == 30


def test_a_carry_edited_on_the_phone_keeps_its_distance_too():
    # 17 Oct 2026: after editing the workout on the phone, Hevy sent reps 0 (not None) with
    # the distance, and all three carry sets were dropped as missed reps.
    w = {"start_time": "2000-01-01T15:00:00Z", "title": "t", "exercises": [
        {"index": 0, "title": "Farmers Walk", "sets": [
            {"index": 0, "weight_kg": 140.6, "reps": 0, "distance_meters": 45,
             "duration_seconds": 0, "type": "normal"}]}]}
    rows, _, _ = hevy_sets([w], {})
    assert rows[0]["reps"] == 45

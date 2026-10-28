import pytest

from estebot import hevy_import as I
from estebot.parsers import hevy

KG = I.LB_TO_KG


def m(name, kind="weight_reps", status="ok", placeholder=""):
    return {"jefit_exercise": name, "hevy_template_id": "T-" + name, "hevy_type": kind,
            "placeholder_lb": placeholder, "status": status}

def row(name, idx, lb, reps, day="2000-01-01", group="back"):
    return {"date": day, "exercise": name, "set_idx": str(idx), "weight_lb": str(lb),
            "reps": str(reps), "muscle_group": group}

def sets_of(payload, i=0):
    return payload["workout"]["exercises"][i]["sets"]


def test_an_unsettled_exercise_stops_the_build():
    with pytest.raises(I.MapError, match="Mystery"):
        I.build([row("Mystery", 1, 50, 5)], {"Mystery": m("Mystery", status="ASK")}, {}, {})


def test_every_workout_is_private_and_tagged_so_the_reader_skips_it():
    [(_, p)], _ = I.build([row("Row", 1, 135, 5)], {"Row": m("Row")}, {}, {})
    assert p["workout"]["is_private"] is True
    assert hevy.is_imported(p["workout"])
    assert hevy.hevy_sets([{**p["workout"], "exercises": []}], {})[0] == []


def test_set_shape_follows_the_hevy_type():
    mp = {"Pull": m("Pull", "reps_only"), "Dip": m("Dip", "bodyweight_weighted", placeholder="10"),
          "Carry": m("Carry", "short_distance_weight")}
    rows = [row("Pull", 1, 10, 8), row("Dip", 1, 10, 12), row("Dip", 2, 45, 8),
            row("Carry", 1, 100, 40)]
    order = {("2000-01-01", n): i for i, n in enumerate(["Pull", "Dip", "Carry"])}
    [(_, p)], rep = I.build(rows, mp, {}, order)
    pull, dip, carry = (sets_of(p, i) for i in range(3))
    assert pull[0]["weight_kg"] is None and pull[0]["reps"] == 8
    assert dip[0]["weight_kg"] == 0 and dip[1]["weight_kg"] == 45 * KG
    assert carry[0]["reps"] is None and carry[0]["distance_meters"] == 40
    assert carry[0]["weight_kg"] == 100 * KG
    assert rep["placeholder loads posted as 0"] == 1


def test_weight_round_trips_to_the_logged_pounds():
    [(_, p)], _ = I.build([row("Row", 1, 47.3, 10)], {"Row": m("Row")}, {}, {})
    assert hevy.kg_to_lb(sets_of(p)[0]["weight_kg"]) == 47.3


def test_a_missed_rep_override_becomes_a_zero_rep_failure_set():
    rows = [row("DL", 1, 200, 1), row("DL", 2, 200, 1)]
    [(_, p)], _ = I.build(rows, {"DL": m("DL")}, {}, {},
                          overrides={("2000-01-01", "DL", 2): ("failure", 0)})
    assert [(s["type"], s["reps"]) for s in sets_of(p)] == [("normal", 1), ("failure", 0)]


def test_skipped_exercises_and_days_already_in_hevy_are_left_out():
    rows = [row("Chop", 1, 20, 10), row("Row", 1, 135, 5, day="2000-01-02")]
    mp = {"Chop": m("Chop", status="skip"), "Row": m("Row")}
    out, rep = I.build(rows, mp, {}, {}, skip_dates={"2000-01-02"})
    assert out == [] and rep["days already in Hevy"] == 1


def test_a_timer_left_running_does_not_set_the_duration():
    start = 946_728_000
    s, e, fell_back = I.times([(start, start + 3600), (start + 7200, start + 7200 + 86400)])
    assert (s, e - s, fell_back) == (start, 3600, False)
    assert I.times([(start, start + 86400)])[2] is True


def test_exercises_follow_the_logged_order():
    rows = [row("A", 1, 10, 5), row("B", 1, 10, 5)]
    [(_, p)], _ = I.build(rows, {"A": m("A"), "B": m("B")}, {},
                          {("2000-01-01", "B"): 1, ("2000-01-01", "A"): 2})
    assert [e["exercise_template_id"] for e in p["workout"]["exercises"]] == ["T-B", "T-A"]

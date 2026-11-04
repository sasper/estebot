"""The pain-or-injury check-in (2 Nov 2026), which replaced the lower-back level. Every
session and number here is made up."""
import json

from estecoach import core
from test_engine import GOALS, _calm_week, _session

PUSH, PULL, LEGS = "chest|shoulders|triceps", "back|biceps|olympic", "quads|hamstrings|calves"


def _pull_then_legs_next(con):
    # push, then pull: legs is next, with squats (spine and knees).
    _session(con, "2026-10-08", LEGS, [("Barbell Squat", 225, 5, 8)] * 4 + [("Leg Extension", 90, 12, 8)] * 3)
    _session(con, "2026-10-11", PUSH, [("Barbell Bench Press", 185, 5, 8)] * 6)
    _session(con, "2026-10-12", PULL, [("Barbell Hang Clean", 135, 3, 8)] * 6)
    for ex, g in (("Barbell Squat", "quads"), ("Leg Extension", "quads"),
                  ("Barbell Bench Press", "chest"), ("Barbell Hang Clean", "olympic")):
        con.execute("INSERT INTO e1rm (exercise, muscle_group, date) VALUES (?,?,'2026-10-01')", (ex, g))


def _push_next(con):
    # pull, then legs: push is next, bench only (no spinal loading, no knees).
    _session(con, "2026-10-08", PUSH, [("Barbell Bench Press", 185, 5, 8)] * 6)
    _session(con, "2026-10-11", PULL, [("Barbell Hang Clean", 135, 3, 8)] * 6)
    _session(con, "2026-10-12", LEGS, [("Barbell Squat", 225, 5, 8)] * 6)
    for ex, g in (("Barbell Squat", "quads"), ("Barbell Bench Press", "chest"), ("Barbell Hang Clean", "olympic")):
        con.execute("INSERT INTO e1rm (exercise, muscle_group, date) VALUES (?,?,'2026-10-01')", (ex, g))


def _check_in(con, day, pain, energy=2):
    con.execute("INSERT INTO checkin (date, energy, pain, back, back_reported, source) VALUES (?,?,?,?,1,'manual')",
                (day, energy, json.dumps(pain), core.PAIN_TO_BACK[pain.get("back", 0)]))


def _call(con, day="2026-10-14"):
    from estecoach.analysis import engine
    return engine.readiness(con, day, GOALS, voting=())


# ---------------------------------------------------------------- the rule itself
def test_the_pain_rule_by_severity_and_use():
    used = {"back", "knees"}
    none = {"rest": [], "deload": [], "notes": []}
    assert core.pain_rule({"knees": 3}, used) == dict(none, rest=["knees"])
    assert core.pain_rule({"knees": 3}, set()) == dict(none, notes=["knees"])   # not loaded today
    assert core.pain_rule({"knees": 2}, used)["deload"] == ["knees"]
    assert core.pain_rule({"arms": 2}, used) == dict(none, notes=["arms"])
    assert core.pain_rule({"back": 1, "knees": 1}, used)["notes"] == ["back", "knees"]
    assert core.pain_rule({"arms": 2}, None)["deload"] == ["arms"]          # workout unknown: all used
    assert core.pain_rule({"other": 2}, set())["deload"] == ["other"]       # "other" is always used
    assert core.pain_rule({}, used) == core.pain_rule(None, used) == none


def test_the_mapping_file_names_only_known_areas():
    areas = {a for a, _ in core.PAIN_AREAS} - {"back", "other"}
    groups = core.load_pain_groups()
    assert groups and all(set(v) <= areas for v in groups.values())


# ---------------------------------------------------------------- the call
def test_pain_that_hurts_to_move_where_the_workout_loads_is_rest(con):
    from estecoach.analysis import engine
    _calm_week(con); _push_next(con)
    _check_in(con, "2026-10-14", {"arms": 3})                   # bench loads the arms
    r = _call(con)
    assert (r["verdict"], r["rule"]) == ("rest", "pain")
    assert engine.recommend_pattern(con, "2026-10-14", r["checkin"])[1] == \
        "Rest today. If it isn't improving, get it looked at."


def test_pain_that_hurts_to_move_where_the_workout_doesnt_load_is_a_note(con):
    from estecoach.analysis import engine
    _calm_week(con); _push_next(con)
    _check_in(con, "2026-10-14", {"ankles": 3})                 # push day doesn't load the ankles
    r = _call(con)
    assert r["verdict"] == "normal" and r["holds"] == []
    assert engine.recommend_pattern(con, "2026-10-14", r["checkin"]) is None
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    assert plan["day_adjust"] is None and all(e["action"] != "deload" for e in plan["exercises"])


def test_back_pain_on_leg_day_deloads_the_squat_and_nothing_else(con):
    # The squat loads the spine and gets the back's 60%; leg extensions don't, and keep
    # their target. The call itself stays as the evidence makes it.
    from estecoach.analysis import engine
    _calm_week(con); _pull_then_legs_next(con)
    _check_in(con, "2026-10-14", {"back": 2})
    r = _call(con)
    assert r["verdict"] == "normal" and r["holds"] == []
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    by = {e["exercise"]: e for e in plan["exercises"]}
    assert by["Barbell Squat"]["action"] == "deload" and by["Barbell Squat"]["deload_factor"] == core.DELOAD_BACK
    assert by["Barbell Squat"]["adjust"] == "Back pain: this lift loads it"
    assert by["Leg Extension"]["action"] != "deload" and "adjust" not in by["Leg Extension"]
    assert plan["deload"] is None and plan["pain_deload"] == {"back": core.DELOAD_BACK}
    assert plan["day_adjust"].startswith(
        "Pain: lifts that load your back are at 60% of last time's sets and weight. Everything else as planned.")


def test_back_pain_on_an_upper_body_day_leaves_the_call_alone(con):
    from estecoach.analysis import engine
    _calm_week(con); _push_next(con)
    _check_in(con, "2026-10-14", {"back": 2})
    r = _call(con)
    feel = next(x for x in r["signals"] if x["area"] == "feel")
    assert r["verdict"] == "normal" and r["holds"] == [] and feel["level"] == "green"
    assert "back: limits some movements today" in feel["reasons"]               # shown as a note
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    assert plan["deload"] is None and plan["exercises"][0]["action"] != "deload"


def test_knee_pain_on_leg_day_deloads_every_lift_that_loads_the_knees(con):
    from estecoach.analysis import engine
    _calm_week(con); _pull_then_legs_next(con)
    _check_in(con, "2026-10-14", {"knees": 2})
    r = _call(con)
    assert r["verdict"] == "normal" and r["holds"] == []
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    assert {e["exercise"]: e.get("deload_factor") for e in plan["exercises"]} == {
        "Barbell Squat": core.DELOAD, "Leg Extension": core.DELOAD}   # 80%, not the back's 60%


def test_back_and_knee_pain_take_each_lift_to_its_lowest(con):
    from estecoach.analysis import engine
    _calm_week(con); _pull_then_legs_next(con)
    _check_in(con, "2026-10-14", {"back": 2, "knees": 2})
    r = _call(con)
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    by = {e["exercise"]: e for e in plan["exercises"]}
    assert by["Barbell Squat"]["deload_factor"] == core.DELOAD_BACK and by["Leg Extension"]["deload_factor"] == core.DELOAD
    assert by["Barbell Squat"]["adjust"] == "Back and knees pain: this lift loads them"
    assert plan["day_adjust"].startswith("Pain: lifts that load your back are at 60% of last time's sets and "
                                         "weight, and lifts that load your knees at 80%. Everything else as planned.")


def test_an_unknown_workout_counts_every_area_as_used(con):
    _calm_week(con)                                             # no lifting history: no plan
    _check_in(con, "2026-10-14", {"arms": 3})
    assert _call(con)["verdict"] == "rest"


def test_noticeable_pain_is_a_note_and_still_a_good_check_in(con):
    from estecoach.analysis import engine
    _calm_week(con); _pull_then_legs_next(con)
    _check_in(con, "2026-10-14", {"back": 1}, energy=3)
    r = _call(con)
    feel = next(x for x in r["signals"] if x["area"] == "feel")
    assert r["verdict"] == "normal" and feel["level"] == "green" and r["holds"] == []
    assert r["hard_reasons"]["check-in"][0]                     # still counts toward going hard
    plan = engine.training_plan(con, "2026-10-14", r["verdict"], r["checkin"])
    assert plan["deload"] is None                               # tired back: no 60% any more


def test_pain_yesterday_holds_today_until_a_check_in(con):
    _calm_week(con); _push_next(con)
    _check_in(con, "2026-10-13", {"arms": 2})
    assert _call(con)["holds"] == ["pain yesterday, and no check-in today"]
    _check_in(con, "2026-10-14", {})
    assert _call(con)["holds"] == []


# ---------------------------------------------------------------- old back levels
def test_old_back_levels_read_as_back_pain(con):
    # Rows from before the pain question carry only the back level: spasm is 3 (still a rest
    # day), sore 2, tired 1, ok none. Nothing is rewritten; pain_of reads them.
    from estecoach.analysis import engine
    for day, back in (("2026-10-01", "spasm"), ("2026-10-02", "sore"), ("2026-10-03", "tired"), ("2026-10-04", "ok")):
        con.execute("INSERT INTO checkin (date, back, back_reported, source) VALUES (?,?,1,'derived')", (day, back))
    con.execute("INSERT INTO checkin (date, energy, back_reported, source) VALUES ('2026-10-05', 2, 0, 'derived')")
    got = {r["date"]: engine.pain_of(r) for r in con.execute("SELECT * FROM checkin")}
    assert got == {"2026-10-01": {"back": 3}, "2026-10-02": {"back": 2}, "2026-10-03": {"back": 1},
                   "2026-10-04": {}, "2026-10-05": None}


# ---------------------------------------------------------------- easy days, scored on their own targets
def test_an_easy_day_is_scored_on_its_lighter_targets_apart_from_the_evidence(con):
    from estecoach.analysis import calls, engine
    _session(con, "2026-11-09", PUSH, [("Barbell Bench Press", 185, 4, 7)] * 3 + [("Cable Fly", 40, 10, 7)] * 2)
    targets = [{"exercise": "Barbell Bench Press", "goal": {"weight_lb": 185, "reps": 4}},
               {"exercise": "Cable Fly", "goal": {"weight_lb": 45, "reps": 10}}]
    con.execute("INSERT INTO call_record VALUES ('2026-11-09', 'light', '', ?)", (json.dumps(targets),))
    assert engine.session_outcome(con, "2026-11-09") is None              # still out of the evidence
    assert engine.lighter_outcome(con, "2026-11-09", targets) == {"n": 2, "met": 1, "hit": 0.5}
    got = calls.graded(con, [{"date": "2026-11-09", "verdict": "light", "targets": json.dumps(targets)}])
    assert got == {"lighter": {"n": 1, "hit": 0.5}}

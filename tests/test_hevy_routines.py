import copy, json
from pathlib import Path
from types import SimpleNamespace

import pytest

from estecoach import hevy_routines as H
from estecoach.parsers import hevy

# A made-up routine with the shape Hevy returns: a ramp with a warm-up and a back-off set,
# two supersets, a note, one-set exercise.
RT = json.loads((Path(__file__).parent / "fixtures" / "hevy_routine.json").read_text())["routine"]
NAMES = {"Bench Press (Barbell)": "Barbell Bench Press", "Arnold Press (Dumbbell)": "Dumbbell Seated Arnold Press",
         "Lateral Raise (Dumbbell)": "Dumbbell Lateral Raise", "Triceps Rope Pushdown": "Cable Tricep Pushdown (Rope)",
         "Chest Fly (Machine)": "Machine Chest Fly", "Shrug (Dumbbell)": "Dumbbell Shrug"}
canon = lambda t: NAMES.get(t, t)


def plan(*items, deload=None):
    return {"kind": "push", "variant": "A", "deload": deload, "exercises": list(items)}

BENCH = {"exercise": "Barbell Bench Press", "target": "210 × 2–3", "goal": {"weight_lb": 210, "reps": 2},
         "reps_range": [2, 3], "main": True}
FLY = {"exercise": "Machine Chest Fly", "target": "105 × 9", "goal": {"weight_lb": 105, "reps": 9}}


def sets_lb(rt, title):
    e = next(e for e in rt["exercises"] if e["title"] == title)
    return [(hevy.kg_to_lb(s["weight_kg"] or 0), s["reps"], s.get("rep_range"), s["type"]) for s in e["sets"]]


def test_only_reading_routines_and_updating_one_is_allowed():
    rid = "/routines/00000000-0000-4000-8000-000000000001"
    assert H._allowed("GET", "/routines?page=1&pageSize=10") and H._allowed("GET", rid) and H._allowed("PUT", rid)
    for m, p in [("POST", "/workouts"), ("PUT", "/workouts/00000000-0000-4000-8000-000000000001"),
                 ("GET", "/workouts?page=1&pageSize=10"), ("DELETE", rid), ("POST", "/routines"),
                 ("PUT", "/routines/../workouts/x")]:
        assert not H._allowed(m, p)
        with pytest.raises(H.Refused):
            H._request(m, p, "key")


def test_an_unchanged_routine_writes_back_exactly_what_was_read():
    body = H.put_body(RT)["routine"]
    assert body["title"] == RT["title"] and body["folder_id"] == RT["folder_id"]
    for got, e in zip(body["exercises"], RT["exercises"]):
        assert got == {**{k: e[k] for k in H.EXERCISE_KEYS - {"sets"}},
                       "sets": [{k: s[k] for k in H.SET_KEYS if k in s} for s in e["sets"]]}
    # A plan with nothing in it changes nothing at all.
    same, changes, _ = H.changes_for(RT, plan(), "normal", canon)
    assert json.dumps(H.put_body(same)) == json.dumps(H.put_body(RT)) and changes == []


def test_a_field_hevy_adds_later_is_refused_rather_than_dropped():
    rt = copy.deepcopy(RT)
    rt["exercises"][0]["sets"][0]["rpe"] = 8
    with pytest.raises(H.Refused, match="rpe"):
        H.put_body(rt)


def test_every_target_reads_back_as_the_same_pounds():
    for tenths in range(1, 10001):
        lb = tenths / 10
        kg = H.lb_to_kg(lb)
        assert hevy.kg_to_lb(kg) == lb and hevy.kg_to_lb(round(kg, 3)) == lb


def test_a_normal_day_writes_the_top_set_and_a_note_and_nothing_else():
    new, changes, skipped = H.changes_for(RT, plan(BENCH, FLY), "hard", canon)
    assert sets_lb(new, "Bench Press (Barbell)") == [
        (95, 15, None, "warmup"), (155, 10, None, "normal"), (185, 6, None, "normal"),
        (210, None, {"start": 2, "end": 3}, "normal"), (155, 8, None, "normal")]   # ramp and back-off kept
    bench = next(e for e in new["exercises"] if e["title"] == "Bench Press (Barbell)")
    assert bench["notes"] == "EsteCoach: Go hard · 210 × 2–3\nPause the first rep"         # yours kept
    assert sets_lb(new, "Chest Fly (Machine)")[-1] == (105, None, {"start": 9, "end": 9}, "normal")
    for k in ("rest_seconds", "superset_id", "exercise_template_id"):
        assert [e[k] for e in new["exercises"]] == [e[k] for e in RT["exercises"]]
    assert ("Dumbbell Shrug", "not in today's plan") in skipped
    assert H.preview(RT["title"], changes, skipped).startswith(
        "Will change Push A - Bench: Barbell Bench Press top set 205 × 4 → 210 × 2–3; Machine Chest Fly top set 105 × 8 → 105 × 9.")


def test_a_lighter_target_goes_on_every_heavy_set():
    # Easing back in: 205 x 4 becomes 185. The 185 x 6 under it is a heavy set (80% of 205 or
    # more) and gets the target too; the warm-up, the 155 ramp and the 155 back-off stay.
    ease = dict(BENCH, target="185 × 3–5", goal={"weight_lb": 185, "reps": 3}, reps_range=[3, 5])
    new, changes, _ = H.changes_for(RT, plan(ease), "normal", canon)
    r = {"start": 3, "end": 5}
    assert sets_lb(new, "Bench Press (Barbell)") == [
        (95, 15, None, "warmup"), (155, 10, None, "normal"), (185, None, r, "normal"),
        (185, None, r, "normal"), (155, 8, None, "normal")]
    assert H.preview(RT["title"], changes, []).startswith(
        "Will change Push A - Bench: Barbell Bench Press top set 205 × 4 → 2 sets 185 × 3–5.")
    # A heavier target is for the top set only, as before.
    up, _, _ = H.changes_for(RT, plan(BENCH), "normal", canon)
    assert sets_lb(up, "Bench Press (Barbell)")[2] == (185, 6, None, "normal")


def test_a_note_is_replaced_not_stacked():
    once, _, _ = H.changes_for(RT, plan(BENCH), "hard", canon)
    twice, _, _ = H.changes_for(once, plan(dict(BENCH, target="215 × 1–2")), "normal", canon)
    assert next(e for e in twice["exercises"] if e["title"] == "Bench Press (Barbell)")["notes"] == \
        "EsteCoach: Go as planned · 215 × 1–2\nPause the first rep"


def test_a_skipped_lift_and_bodyweight_work_keep_their_sets():
    pullups = {"exercise": "Barbell Bench Press", "skipped": True, "target": "Skipped today", "goal": None}
    new, changes, skipped = H.changes_for(RT, plan(pullups), "normal", canon)
    assert sets_lb(new, "Bench Press (Barbell)") == sets_lb(RT, "Bench Press (Barbell)")
    assert ("Barbell Bench Press", "marked skip in the check-in") in skipped and changes == []
    rt = copy.deepcopy(RT)
    rt["exercises"][5]["title"] = "Pull Up"
    for s in rt["exercises"][5]["sets"]:
        s["weight_kg"] = None
    pu = {"exercise": "Pull Up", "target": "3 sets, 20–21 reps in total", "goal": {"total_reps": 20}}
    new, changes, _ = H.changes_for(rt, plan(pu), "normal", canon)
    assert new["exercises"][5]["sets"] == rt["exercises"][5]["sets"] and changes[0]["what"] == "note"


def test_an_exercise_listed_twice_gets_the_target_on_the_heavier_one():
    rt = copy.deepcopy(RT)
    rt["exercises"].append(copy.deepcopy(rt["exercises"][4]))
    for s in rt["exercises"][-1]["sets"]:
        s["weight_kg"] = H.lb_to_kg(50)
    new, changes, _ = H.changes_for(rt, plan(FLY), "normal", canon)
    assert [c["what"] for c in changes] == ["top set", "note"]
    assert all(s["weight_kg"] == H.lb_to_kg(50) for s in new["exercises"][-1]["sets"])


def test_a_deload_cuts_the_sets_and_the_next_write_puts_them_back():
    new, changes, _ = H.changes_for(RT, plan(dict(BENCH, target="75, 125, 150, 165"), deload=0.8), "light", canon)
    assert [(w, r) for w, r, *_ in sets_lb(new, "Bench Press (Barbell)")] == [(75, 15), (125, 10), (150, 6), (165, 4)]
    # Hevy's update prompt then saves the light sets; the next write starts from before the deload.
    back = H.restored(new, RT)
    assert sets_lb(back, "Bench Press (Barbell)") == sets_lb(RT, "Bench Press (Barbell)")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = SimpleNamespace(training={"hevy": {"routines": {"push A": RT["id"]}}}, goals={},
                        data_file=lambda n: tmp_path / n, is_demo=False)
    writes = []
    monkeypatch.setattr(H, "get_routine", lambda rid, key: copy.deepcopy(RT))
    monkeypatch.setattr(H, "put_routine", lambda rid, body, key: writes.append(body))
    monkeypatch.setattr(H, "_canon", lambda cfg, day: canon)
    monkeypatch.setattr(H, "_corrected", lambda cfg, day: lambda name: False)
    H._cache.clear()
    c.writes = writes
    return c


def test_the_automatic_write_happens_once_a_day_and_never_on_a_rest_day(cfg):
    assert H.auto(cfg, plan(BENCH), "rest", "k", "2026-11-02") is None and cfg.writes == []
    assert H.auto(cfg, plan(BENCH), "hard", "k", "2026-11-02").startswith("Will change Push A")
    assert H.auto(cfg, plan(BENCH), "hard", "k", "2026-11-02") is None and len(cfg.writes) == 1
    H.send(cfg, plan(BENCH), "hard", "k", "button", "2026-11-02")            # the button always writes
    assert len(cfg.writes) == 2 and H.last_write(cfg, "2026-11-02")["source"] == "button"


def test_after_a_deload_write_the_next_write_restores_the_sets(cfg, monkeypatch):
    H.send(cfg, plan(dict(BENCH, target="light"), deload=0.8), "light", "k", "auto", "2026-11-02")
    assert len(cfg.writes[0]["routine"]["exercises"][0]["sets"]) == 4
    # Hevy now has the deloaded routine (its update prompt saved the light session).
    deloaded = copy.deepcopy(RT)
    deloaded["exercises"][0]["sets"] = deloaded["exercises"][0]["sets"][:4]
    monkeypatch.setattr(H, "get_routine", lambda rid, key: copy.deepcopy(deloaded))
    H.send(cfg, plan(BENCH), "hard", "k", "auto", "2026-11-05")
    bench = cfg.writes[1]["routine"]["exercises"][0]["sets"]
    assert len(bench) == 5 and bench[-1]["weight_kg"] == RT["exercises"][0]["sets"][-1]["weight_kg"]
    assert json.loads(H.load_log(cfg)[1]["before"])["exercises"][0]["sets"] == RT["exercises"][0]["sets"]


def test_the_demo_and_a_config_without_routines_never_write(cfg):
    cfg.is_demo = True
    assert H.auto(cfg, plan(BENCH), "hard", "k", "2026-11-02") is None
    cfg.is_demo = False
    cfg.training = {}
    assert H.auto(cfg, plan(BENCH), "hard", "k", "2026-11-02") is None and cfg.writes == []


def test_adding_a_rep_on_straight_sets_changes_only_the_weakest_set():
    # Dry run on 1 Nov 2026: curls at 40 x 10, 9, 8 with a 40 x 9 target came out as 9, 9, 9.
    rt = copy.deepcopy(RT)
    for s, r in zip(rt["exercises"][2]["sets"], (12, 11, 10)):
        s["reps"] = r
    raise_ = {"exercise": "Dumbbell Lateral Raise", "target": "15 × 11", "goal": {"weight_lb": 15, "reps": 11}}
    new, changes, _ = H.changes_for(rt, plan(raise_), "normal", canon)
    assert [(r, rr) for _, r, rr, _ in sets_lb(new, "Lateral Raise (Dumbbell)")] == [
        (12, None), (11, None), (None, {"start": 11, "end": 11})]
    assert "top set 15 × 10 → 15 × 11" in H.preview(rt["title"], changes, [])


def test_a_note_written_under_the_old_name_is_replaced_too():
    rt = copy.deepcopy(RT)
    rt["exercises"][0]["notes"] = "EsteBot: Go hard · 205 × 4\nPause the first rep"
    new, _, _ = H.changes_for(rt, plan(BENCH), "hard", canon)
    assert new["exercises"][0]["notes"] == "EsteCoach: Go hard · 210 × 2–3\nPause the first rep"


def test_the_jefit_import_tag_keeps_the_wording_the_workouts_carry():
    # The rename changed this string once, and every imported workout stopped being skipped.
    assert hevy.IMPORT_TAG == "Imported from Jefit by EsteBot"


def test_a_pain_deload_cuts_only_the_lift_that_loads_the_area(cfg):
    # Shoulder pain at 2 deloads the press that loads it; the fly beside it keeps its target.
    press = dict(BENCH, action="deload", target="light", deload_factor=0.8)
    H.send(cfg, plan(press, FLY), "normal", "k", "auto", "2026-11-02")
    new = cfg.writes[0]["routine"]
    bench = next(e for e in new["exercises"] if e["exercise_template_id"] == RT["exercises"][0]["exercise_template_id"])
    assert len(bench["sets"]) == 4
    fly = next(e for e in RT["exercises"] if canon(e["title"]) == "Machine Chest Fly")
    got = next(e for e in new["exercises"] if e["exercise_template_id"] == fly["exercise_template_id"])
    assert len(got["sets"]) == len(fly["sets"]) and 105 in [round(hevy.kg_to_lb(s["weight_kg"] or 0)) for s in got["sets"]]
    assert H.load_log(cfg)[0]["deload"] == "0.8"          # so the next write restores the press

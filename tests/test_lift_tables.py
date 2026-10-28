from estebot.parsers import lift_tables as L


def _set(d, ex, group, wt, reps, e1rm, rpe="", set_type="normal"):
    return {"date": d, "exercise": ex, "muscle_group": group, "weight_lb": wt, "reps": reps,
            "volume_lb": wt * reps, "e1rm_lb": e1rm, "rpe": rpe, "set_type": set_type}


SETS = [
    _set("2026-03-02", "Bench Press", "chest", 95.0, 10, 0.0, "5", "Warmup"),
    _set("2026-03-02", "Bench Press", "chest", 185.0, 5, 215.8, "8"),
    _set("2026-03-02", "Bench Press", "chest", 175.0, 6, 210.0, ""),
    _set("2026-03-02", "Barbell Row", "back", 155.0, 8, 196.3, "7"),
    _set("2026-03-09", "Bench Press", "chest", 190.0, 5, 221.7, "9"),
]


def test_the_session_table_skips_warmups_whatever_their_case():
    # jefit.py and demo.py each built this table, and only jefit.py lowercased set_type: a
    # "Warmup" set would have counted toward mean RPE in one and not the other.
    rows = L.session_rows(SETS, minutes={"2026-03-02": 61.5}, kcal={})
    assert rows[0] == ["2026-03-02", 2, 4, 4165, 185.0, "back|chest", 61.5, "", 3, 7.5]
    assert rows[1][6] == "" and rows[1][9] == 9.0       # no minutes for that day: blank


def test_weekly_volume_and_progression_layouts():
    # The real history and the demo write these through one builder now; this pins the
    # rows and rounding both used to produce on their own.
    weeks = L.weekly_volume_rows(SETS, ["chest", "back", "other"])
    assert weeks == [["2026-W10", 2925, 1240, 0, 3, 1, 0, 4165, 4],
                     ["2026-W11", 950, 0, 0, 1, 0, 0, 950, 1]]
    assert len(L.weekly_volume_cols(["chest", "back", "other"])) == len(weeks[0])
    prog = L.progression_rows(SETS)
    assert prog[1] == ["Bench Press", "chest", "2026-03-02", 215.8, 185.0, 5, 2925, 3]
    assert [r[:3] for r in prog] == [["Barbell Row", "back", "2026-03-02"],
                                     ["Bench Press", "chest", "2026-03-02"],
                                     ["Bench Press", "chest", "2026-03-09"]]

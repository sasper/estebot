from estebot.parsers.jefit_raw import load_renames, rename


def test_a_spelling_change_across_the_gap_is_one_series(tmp_path):
    # Jefit renamed the lift between eras, so 2017-19 and 2022-26 sat under two keys
    # and blocks.py's nine-year series broke exactly at the longest gap in the data.
    p = tmp_path / "renames.csv"
    p.write_text("from_date,to_date,exercise,new_name,note\n"
                 "2017-01-01,2019-12-31,Barbell Bent Over Row,Barbell Bent-Over Row,spelling\n")
    rules = load_renames(str(p))
    assert rename(rules, "2018-03-05", "Barbell Bent Over Row") == "Barbell Bent-Over Row"
    assert rename(rules, "2023-03-06", "Barbell Bent-Over Row") == "Barbell Bent-Over Row"


def test_a_rename_outside_its_dates_leaves_the_name_alone(tmp_path):
    # Renames are dated so a later, different exercise under an old name is never fused in.
    p = tmp_path / "renames.csv"
    p.write_text("from_date,to_date,exercise,new_name,note\n"
                 "2017-01-01,2019-12-31,Pull Ups,Pull-Up,spelling\n")
    assert rename(load_renames(str(p)), "2022-05-01", "Pull Ups") == "Pull Ups"


def test_a_hevy_title_can_change_exercise_from_a_date(tmp_path):
    # Hevy's "Farmers Walk" maps to the uphill carry for good. From 17 Oct 2026 the carries
    # are done flat - heavier, not comparable - so a dated rule starts a new series, for Hevy
    # sets only; Jefit's own rules still never touch Hevy and vice versa.
    p = tmp_path / "renames.csv"
    p.write_text("from_date,to_date,exercise,new_name,source,note\n"
                 "2026-10-17,,Trap Bar Carry (uphill),Trap Bar Carry (flat),hevy,flat from here\n"
                 "2017-01-01,2019-12-31,Pull Ups,Pull-Up,,spelling\n")
    rules = load_renames(str(p))
    assert rename(rules, "2026-10-17", "Trap Bar Carry (uphill)", "hevy") == "Trap Bar Carry (flat)"
    assert rename(rules, "2026-10-14", "Trap Bar Carry (uphill)", "hevy") == "Trap Bar Carry (uphill)"
    assert rename(rules, "2026-10-17", "Trap Bar Carry (uphill)") == "Trap Bar Carry (uphill)"
    assert rename(rules, "2018-03-05", "Pull Ups", "hevy") == "Pull Ups"

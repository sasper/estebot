from estebot.parsers.jefit_raw import correct, load_corrections


def test_a_ranged_rule_removes_jefits_default_pull_up_load(tmp_path):
    # Jefit stored +10 lb on nearly every pull-up set from 2017; none were weighted.
    p = tmp_path / "corrections.csv"
    p.write_text("date,to_date,exercise,factor,min_weight,note\n"
                 "2017-01-01,2026-09-30,Pull-Up,0,0,default load\n")
    rules = load_corrections(str(p))
    assert correct(rules, "2018-06-25", "Pull-Up", 10.0) == (0.0, True)
    assert correct(rules, "2026-10-14", "Pull-Up", 10.0) == (10.0, False)   # after the range


def test_a_rule_without_to_date_still_covers_only_its_day(tmp_path):
    # The 2 Sep 2026 combined-dumbbell rules predate to_date and must keep working as is.
    p = tmp_path / "corrections.csv"
    p.write_text("date,exercise,factor,min_weight,note\n"
                 "2026-09-02,Dumbbell Bench Press,0.5,150,combined\n")
    rules = load_corrections(str(p))
    assert correct(rules, "2026-09-02", "Dumbbell Bench Press", 200.0) == (100.0, True)
    assert correct(rules, "2026-09-02", "Dumbbell Bench Press", 90.0) == (90.0, False)  # min_weight
    assert correct(rules, "2026-09-03", "Dumbbell Bench Press", 200.0) == (200.0, False)


def test_a_wrong_bar_weight_is_added_back_on_the_right_source_only(tmp_path):
    # The open hex bar is 55 lb; sessions logged it as 45. 5 Oct came from Jefit, 14 Oct
    # from Hevy, and each rule must touch only its own source's sets.
    p = tmp_path / "corrections.csv"
    p.write_text("date,to_date,exercise,factor,min_weight,add_lb,source,note\n"
                 "2026-10-05,,Trap Bar Carry (uphill),1,0,10,,hex bar 55 lb\n"
                 "2026-10-14,,Trap Bar Carry (uphill),1,0,10,hevy,hex bar 55 lb\n")
    rules = load_corrections(str(p))
    assert correct(rules, "2026-10-05", "Trap Bar Carry (uphill)", 135.0) == (145.0, True)
    assert correct(rules, "2026-10-05", "Trap Bar Carry (uphill)", 135.0, "hevy") == (135.0, False)
    assert correct(rules, "2026-10-14", "Trap Bar Carry (uphill)", 235.0, "hevy") == (245.0, True)
    assert correct(rules, "2026-10-14", "Trap Bar Carry (uphill)", 235.0) == (235.0, False)

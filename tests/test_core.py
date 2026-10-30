"""Every test here corresponds to a bug that actually occurred in this project."""
from datetime import date
from datetime import datetime as dt

import pytest

from estebot.core import (
    dedupe_daily_total,
    drop_subset_duplicates,
    epley_e1rm,
    is_hard_set,
    night_of,
    ols_slope_per_week,
    parse_jefit_logs,
    rolling,
    trailing_mean,
    weeks_to_goal,
    union_minutes,
)


class TestUnionMinutes:
    """Apple Health emits overlapping sleep records; summing them double-counts."""

    def test_single_interval(self):
        assert union_minutes([(dt(2026, 1, 1, 23, 0), dt(2026, 1, 2, 7, 0))]) == 480.0

    def test_disjoint_intervals_add(self):
        spans = [(dt(2026, 1, 1, 23, 0), dt(2026, 1, 2, 1, 0)),
                 (dt(2026, 1, 2, 3, 0), dt(2026, 1, 2, 5, 0))]
        assert union_minutes(spans) == 240.0

    def test_overlap_counted_once(self):
        # the actual bug: Watch and iPhone reporting the same night
        spans = [(dt(2026, 1, 1, 23, 0), dt(2026, 1, 2, 5, 0)),
                 (dt(2026, 1, 2, 1, 0), dt(2026, 1, 2, 7, 0))]
        assert union_minutes(spans) == 480.0      # not 720

    def test_fully_contained_interval_adds_nothing(self):
        spans = [(dt(2026, 1, 1, 22, 0), dt(2026, 1, 2, 8, 0)),
                 (dt(2026, 1, 2, 1, 0), dt(2026, 1, 2, 2, 0))]
        assert union_minutes(spans) == 600.0

    def test_abutting_intervals_merge(self):
        spans = [(dt(2026, 1, 1, 23, 0), dt(2026, 1, 2, 1, 0)),
                 (dt(2026, 1, 2, 1, 0), dt(2026, 1, 2, 3, 0))]
        assert union_minutes(spans) == 240.0

    def test_empty_and_degenerate(self):
        assert union_minutes([]) == 0.0
        assert union_minutes([(dt(2026, 1, 1, 1, 0), dt(2026, 1, 1, 1, 0))]) == 0.0

    def test_unsorted_input(self):
        spans = [(dt(2026, 1, 2, 3, 0), dt(2026, 1, 2, 5, 0)),
                 (dt(2026, 1, 1, 23, 0), dt(2026, 1, 2, 1, 0))]
        assert union_minutes(spans) == 240.0


class TestNightOf:
    """A calendar date splits a night in half; the 12-hour shift fixes it."""

    @pytest.mark.parametrize("stamp,expected_day", [
        (dt(2026, 9, 1, 23, 0), 1),   # fell asleep before midnight
        (dt(2026, 9, 2, 3, 0), 1),    # middle of that same night
        (dt(2026, 9, 2, 7, 0), 1),    # woke the next morning
        (dt(2026, 9, 2, 11, 59), 1),  # just inside the cutoff
        (dt(2026, 9, 2, 12, 0), 2),   # a nap belongs to the coming night
    ])
    def test_night_attribution(self, stamp, expected_day):
        assert night_of(stamp).day == expected_day

    def test_whole_night_lands_on_one_date(self):
        segments = [dt(2026, 9, 1, 23, 30), dt(2026, 9, 2, 2, 0), dt(2026, 9, 2, 6, 45)]
        assert len({night_of(s) for s in segments}) == 1


class TestDedupeDailyTotal:
    def test_takes_largest_source_not_the_sum(self):
        # Watch 9,500 + iPhone 4,200 would report 13,700 steps for one day
        assert dedupe_daily_total({"Watch": 9500.0, "iPhone": 4200.0}) == 9500.0

    def test_single_source_passes_through(self):
        assert dedupe_daily_total({"Watch": 8000.0}) == 8000.0

    def test_no_sources(self):
        assert dedupe_daily_total({}) == 0.0


class TestEpley:
    def test_known_value(self):
        assert epley_e1rm(225, 5) == pytest.approx(262.5)

    def test_single_rep_is_the_weight(self):
        assert epley_e1rm(315, 1) == pytest.approx(325.5)

    def test_above_rep_cap_returns_none_not_zero(self):
        # returning 0.0 here silently corrupted a median over the column
        assert epley_e1rm(155, 12) is None

    def test_cap_boundary_inclusive(self):
        assert epley_e1rm(155, 10) is not None

    @pytest.mark.parametrize("w,r", [(0, 5), (-10, 5), (225, 0), (225, -3)])
    def test_rejects_nonsense(self, w, r):
        assert epley_e1rm(w, r) is None


class TestParseJefitLogs:
    def test_multiple_sets(self):
        assert parse_jefit_logs("225x8,225x6,225x5") == [(225.0, 8), (225.0, 6), (225.0, 5)]

    def test_decimal_weights(self):
        assert parse_jefit_logs("47.25x12,53.75x8") == [(47.25, 12), (53.75, 8)]

    def test_bodyweight_sets_kept(self):
        assert parse_jefit_logs("0x6") == [(0.0, 6)]

    def test_skips_garbage_without_failing(self):
        assert parse_jefit_logs("225x8,,junk,135x10") == [(225.0, 8), (135.0, 10)]

    def test_empty_and_none(self):
        assert parse_jefit_logs("") == []
        assert parse_jefit_logs(None) == []


class TestDropSubsetDuplicates:
    def test_drops_the_partial_row(self):
        # observed on 2017-07-24: one row held "145x6", another the full session
        rows = ["145x6", "145x6,145x6,145x6"]
        assert drop_subset_duplicates(rows) == ["145x6,145x6,145x6"]

    def test_keeps_genuinely_different_rows(self):
        rows = ["225x8,225x6", "185x10,185x10"]
        assert sorted(drop_subset_duplicates(rows)) == sorted(rows)

    def test_identical_rows_collapse(self):
        assert drop_subset_duplicates(["225x8", "225x8"]) == ["225x8"]


def test_exports_order_by_filename_date_not_mtime():
    # A git clone gives every archived export the same fresh mtime, so the incoming
    # (mtime) order is arbitrary. The date in the name must decide.
    from estebot.core import order_by_name_date
    shuffled = ["/a/x_20261012.csv", "/a/x_20260902.csv", "/a/x_20261005.csv"]
    assert order_by_name_date(shuffled) == ["/a/x_20260902.csv", "/a/x_20261005.csv",
                                            "/a/x_20261012.csv"]
    assert order_by_name_date(["/a/b.csv", "/a/a.csv"]) == ["/a/b.csv", "/a/a.csv"]


class TestWeightTrend:
    """Weigh-ins are irregular: gaps from a week to two months, and days with more than one
    reading. Both trend functions must work in calendar time, not by index."""

    def test_slope_is_per_calendar_week_across_a_gap(self):
        # exactly -1 lb/week, sampled the way the real log is: two days, then a 16-day gap
        pts = [(date(2026, 7, 25), 200.0), (date(2026, 7, 26), 200 - 1 / 7),
               (date(2026, 8, 11), 200 - 17 / 7)]
        assert ols_slope_per_week(pts) == pytest.approx(-1.0)

    def test_no_slope_from_too_few_points_or_a_single_day(self):
        assert ols_slope_per_week([(date(2026, 8, 11), 190.0), (date(2026, 8, 25), 189.0)]) is None
        same_day = [(date(2026, 10, 12), 187.0), (date(2026, 10, 12), 186.6),
                    (date(2026, 10, 12), 186.4)]
        assert ols_slope_per_week(same_day) is None

    def test_rolling_mean_does_not_reach_back_across_a_gap(self):
        pts = [(date(2026, 7, 26), 200.0), (date(2026, 8, 11), 190.0), (date(2026, 8, 12), 188.0)]
        # 16 days on, the 14-day window holds only the post-gap readings
        assert rolling(pts, 14) == [(date(2026, 7, 26), 200.0), (date(2026, 8, 11), 190.0),
                                    (date(2026, 8, 12), 189.0)]


class TestBodyweightTrend:
    """Pull-up load is the 14-day weight trend on the session date, weigh-in or not."""

    def test_trend_on_a_day_without_a_weigh_in(self):
        pts = [(date(2026, 10, 11), 186.4), (date(2026, 10, 12), 186.6), (date(2026, 10, 13), 185.5)]
        assert trailing_mean(pts, date(2026, 10, 14)) == pytest.approx((186.4 + 186.6 + 185.5) / 3)

    def test_no_estimate_inside_a_long_gap(self):
        # Two months with no weigh-in. A pull-up session in the middle of the gap has no
        # honest body weight, so it gets no e1RM rather than a stale one.
        pts = [(date(2026, 1, 5), 200.0), (date(2026, 3, 12), 195.0)]
        assert trailing_mean(pts, date(2026, 2, 10)) is None


class TestWeeksToGoal:
    """The goal forecast: a straight line at the current pace, or no date at all."""

    def test_weeks_at_the_current_pace(self):
        # 226.8 lb trend, goal 180, losing 1.6 lb/week -> 29.25 weeks
        assert weeks_to_goal(226.8, -1.6, 180.0) == pytest.approx(29.25)

    def test_no_date_when_the_trend_is_flat_or_moving_away(self):
        assert weeks_to_goal(226.8, 0.0, 180.0) is None
        assert weeks_to_goal(226.8, 0.4, 180.0) is None      # gaining, goal is below
        assert weeks_to_goal(226.8, None, 180.0) is None     # fewer than 3 weigh-ins


class TestHardSet:
    """Hevy logs RPE on most sets; Jefit never did. Unknown effort must not read as easy."""

    def test_rpe_decides_where_it_was_logged(self):
        assert is_hard_set("8.5", "normal") and is_hard_set(7, "failure")
        assert not is_hard_set("6", "normal")

    def test_warmups_never_count_and_no_rpe_counts_as_working(self):
        assert not is_hard_set("", "warmup")
        assert is_hard_set("", "")        # every Jefit-era set


class TestPace:
    """Weight change against the planned loss range (estebot.toml, % of body weight/week)."""

    def test_a_real_loss_can_still_be_too_slow(self):
        from estebot.core import pace
        # -0.62 lb at 200 lb is 0.31 %/week: under the 0.5 floor -> losing less than expected
        assert pace(-0.62, 200.0, 0.5, 1.0)[0] == "slow"
        assert pace(-1.62, 200.0, 0.5, 1.0)[0] == "on_pace"
        assert pace(-3.5, 200.0, 0.5, 1.0)[0] == "fast"
        assert pace(0.4, 200.0, 0.5, 1.0)[0] == "gaining"
        assert pace(None, 200.0, 0.5, 1.0) == (None, None)


class TestCombineSignals:
    """The readiness call counts signals across areas; no single input decides."""

    def test_the_counting_rule(self):
        from estebot.core import combine_signals
        g = {"load": "green", "sleep": "green", "recovery": "green"}
        assert combine_signals({**g, "feel": "amber"}) == "normal"          # a sore back
        assert combine_signals({**g, "sleep": "amber", "nutrition": "amber"}) == "light"
        assert combine_signals({**g, "load": "red"}) == "light"
        assert combine_signals({**g, "load": "red", "recovery": "red"}) == "rest"
        assert combine_signals({**g, "effort": None, "pace": "green"}) == "hard"
        assert combine_signals({**g, "feel": "red"}, forced_rest=True) == "rest"   # back spasm

    def test_go_all_out_needs_recovery_sleep_and_load_actually_measured(self):
        from estebot.core import combine_signals
        assert combine_signals({"load": "green", "sleep": None, "recovery": "green"}) == "normal"

    def test_the_score_never_contradicts_the_verdict(self):
        # An average of the levels put six good areas and one strained at 86 beside "take it
        # easy". Every combination of seven areas has to land inside its verdict's band.
        from itertools import product
        from estebot.core import combine_signals, readiness_score, SCORE_BANDS
        areas = ("load", "effort", "sleep", "recovery", "feel", "nutrition", "pace")
        for combo in product(("green", "amber", "red", None), repeat=len(areas)):
            levels = dict(zip(areas, combo))
            v = combine_signals(levels)
            lo, hi = SCORE_BANDS[v]
            assert lo <= readiness_score(levels, v) <= hi
        today = dict.fromkeys(areas, "green") | {"load": "red"}
        assert readiness_score(today, "light") == 46               # top of "take it easy"


class TestMonotony:
    def test_a_week_of_alike_days_is_monotonous_and_a_light_day_breaks_it(self):
        from estebot.core import monotony
        # a run of training days, ~120 exercise minutes each
        assert monotony([118, 121, 125, 119, 120, 122, 120]) > 2.5
        # a near-idle day, then six varied days
        assert monotony([7, 110, 133, 161, 86, 73, 214]) < 2.0
        assert monotony([60] * 6) is None                  # needs a full week

    def test_seven_identical_days_are_the_most_monotonous_not_unmeasured(self):
        from estebot.core import monotony
        assert monotony([120] * 7) == float("inf")
        assert monotony([0] * 7) is None                   # no training: no monotony


class TestWeeklyShift:
    def test_a_week_of_low_nights_registers_against_a_noisy_baseline(self):
        from estebot.core import weekly_shift_z
        base = [52, 61, 45, 58, 49, 63, 50, 56, 47, 60, 54, 44, 59, 51, 57]   # sd ~5.6
        week = [49, 50, 47, 51, 48, 50, 49]          # every night ~5 ms under the mean
        # -0.8 against single-night spread, never amber; as a week's mean it is -2.1
        assert weekly_shift_z(week, base) < -1.5
        assert abs(weekly_shift_z([55, 53, 56, 54], base)) < 1   # an ordinary week

    def test_too_few_readings_is_no_data(self):
        from estebot.core import weekly_shift_z
        assert weekly_shift_z([50, 51], [50] * 10) is None
        assert weekly_shift_z([50, 51, 52], [50, 51, 52, 53]) is None
        assert weekly_shift_z([50, 51, 52], [50] * 10) is None    # flat baseline


class TestGoalMet:
    def test_heavier_for_the_same_reps_beats_the_plan(self):
        # A session is graded on what was lifted against the plan's target; going up in
        # load for the same reps did more than asked and must not read as a miss.
        from estebot.core import goal_met, progression
        goal = progression("Barbell Squat", [(405, 3, 8.5)])["goal"]        # 405 x 4
        assert goal == {"weight_lb": 405, "reps": 4}
        assert goal_met(goal, [(225, 5, None), (405, 4, 9)])
        assert goal_met(goal, [(415, 4, 9.5)])
        assert not goal_met(goal, [(405, 3, 9), (405, 3, 9)])

    def test_bodyweight_work_is_graded_on_total_reps(self):
        from estebot.core import goal_met, progression
        goal = progression("Pull Up", [(0, 8, 8), (0, 7, 8), (0, 6, 9)])["goal"]
        assert goal == {"total_reps": 22}
        assert goal_met(goal, [(0, 9, None), (0, 7, None), (0, 6, None)])
        assert not goal_met(goal, [(0, 8, None), (0, 7, None), (0, 6, None)])


class TestFlagEffect:
    def test_only_a_band_wholly_below_zero_votes(self):
        # Readiness inputs voted for years without ever being checked against sessions; on
        # 689 of them none predicted anything. An input earns a vote by showing an effect.
        from estebot.core import flag_effect
        worse = flag_effect([-0.2, -0.1, -0.15, -0.25] * 5, [0.0, 0.05, -0.05, 0.02] * 5)
        assert worse["status"] == "predicts worse sessions" and worse["votes"]
        noise = flag_effect([-0.3, 0.2, 0.1, -0.2] * 5, [0.25, -0.3, -0.1, 0.2] * 5)
        assert noise["status"] == "no effect seen" and not noise["votes"]
        better = flag_effect([0.2, 0.1, 0.15, 0.25] * 5, [0.0, 0.05, -0.05, 0.02] * 5)
        assert better["status"] == "goes with better sessions" and not better["votes"]

    def test_a_handful_of_sessions_is_not_judged(self):
        from estebot.core import flag_effect
        e = flag_effect([-0.5] * 10, [0.1] * 40)
        assert e["status"] == "not enough data" and not e["votes"] and e["diff"] is None


class TestEvidenceCall:
    # Every area used to vote, and over 689 sessions the calls didn't separate outcomes:
    # 78% of targets met after "go all out", 77% after "take it easy".
    def test_only_areas_that_earned_a_vote_count(self):
        from estebot.core import evidence_call
        levels = {"sleep": "red", "load": "red", "recovery": "green"}
        assert evidence_call(levels, voting=[]) == "normal"
        assert evidence_call(levels, voting=["sleep"]) == "light"
        assert evidence_call(levels, voting=["sleep", "load"]) == "rest"

    def test_nothing_flagged_is_not_a_reason_to_go_hard(self):
        from estebot.core import evidence_call
        green = {"sleep": "green", "recovery": "green"}
        assert evidence_call(green, voting=["sleep"]) == "normal"
        assert evidence_call(green, voting=["sleep"], positive="a strong last session") == "hard"
        assert evidence_call({"sleep": "amber"}, voting=["sleep"], positive="a strong last session") == "normal"

    def test_safety_rules_hold_whatever_the_evidence(self):
        from estebot.core import evidence_call
        assert evidence_call({}, voting=[], holds=["sick today or yesterday"], positive="x") == "light"
        assert evidence_call({}, voting=[], forced_rest=True) == "rest"


class TestStrengthStandards:
    def test_a_heavy_lifter_is_not_underrated_by_a_plain_ratio(self):
        # A 470 lb squat at 260 lb is 1.8x, which plain ratio bands call intermediate
        # (advanced at 2.0x). Scaled by bodyweight^(2/3) it is advanced.
        from estebot.core import strength_level, strength_thresholds
        assert 470 / 260 < 2.0
        assert strength_level("squat", 470, 260)[0] == "advanced"
        # at the reference bodyweight the bands are the plain ratios
        assert [round(t) for t in strength_thresholds("squat", 180)] == [180, 270, 360, 450]

    def test_levels_run_from_beginner_to_elite_with_the_next_threshold(self):
        from estebot.core import strength_level
        assert strength_level("bench", 100, 180) == ("beginner", 135.0)
        assert strength_level("bench", 400, 180) == ("elite", None)

    def test_dots_rewards_the_same_total_at_a_lower_bodyweight(self):
        from estebot.core import dots
        assert round(dots(1200, 300)) == 301
        assert round(dots(1200, 240)) == 324
        assert dots(1200, 600) == dots(1200, 500)     # clamped above 210 kg


class TestProgression:
    def test_a_grinder_holds_the_load(self):
        # A press triple at RPE 10. The advice given by hand was to hold the load and make it
        # cleaner; the page must say the same, not add weight.
        from estebot.core import progression
        p = progression("Barbell Military Press", [(135, 8, 8), (155, 6, 8.5), (175, 4, 9),
                                                   (185, 3, 10), (135, 10, 10)])
        assert p["action"] == "hold" and p["target"] == "185 × 3 at RPE 9"

    def test_strength_work_reads_the_best_set_at_the_top_weight(self):
        # A single then a triple at the same weight, both RPE 9. The first draft read the
        # single and prescribed a double, below what had already been done.
        from estebot.core import progression
        p = progression("Sumo Deadlift", [(185, 8, 6), (275, 6, 7.5), (315, 1, 9), (315, 3, 9)])
        assert p["target"] == "315 × 4"

    def test_high_rep_work_keeps_its_reps_when_load_goes_up(self):
        # Dumbbell rows for 20, done high-rep on purpose. The first draft read 20 as past
        # the 8-12 range and prescribed 5 lb more for 8.
        from estebot.core import progression
        p = progression("Dumbbell Row", [(60, 20, 7), (60, 20, 7.5), (60, 20, 8)])
        assert p["action"] == "add load" and p["target"] == "65 × 20"

    def test_accessories_add_a_rep_then_load_at_the_top_of_the_range(self):
        from estebot.core import progression
        assert progression("Dumbbell Bench Press", [(70, 10, 7.5), (80, 6, 9)])["target"] == "80 × 7"
        assert progression("Dumbbell Tricep Extension", [(40, 12, 8)] * 3)["target"] == "45 × 8"

    def test_unknown_effort_is_repeated_not_progressed(self):
        # Carries carry no RPE, and their reps column is yards.
        from estebot.core import progression
        p = progression("Trap Bar Carry (flat)", [(135, 90, None), (225, 45, None)])
        assert p["action"] == "repeat" and p["target"].startswith("225 × 45 yd")

    def test_bodyweight_work_progresses_total_reps(self):
        from estebot.core import progression
        p = progression("Pull-Up", [(0, 6, 7)] * 4 + [(0, 6, 9)] * 3)
        assert p["target"] == "7 sets, 43–44 reps in total"

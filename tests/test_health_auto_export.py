import pytest

from estebot.parsers import health_auto_export as H

T0 = "2000-01-02 08:00:00 -0500"


def doc(*metrics, workouts=()):
    return {"data": {"metrics": [{"name": n, "units": u, "data": d} for n, u, d in metrics],
                     "workouts": list(workouts)}}


def test_spo2_percent_becomes_the_fraction_the_full_export_uses():
    days, _, _ = H.parse_metrics([doc(("blood_oxygen_saturation", "%",
                                       [{"date": T0, "qty": 95}, {"date": T0[:11] + "09:00:00 -0500", "qty": 97}]))])
    total, n = days["2000-01-02"][1]["spo2"]
    assert round(total / n, 2) == 0.96


def test_a_changed_unit_preference_fails_loudly():
    with pytest.raises(H.UnitError, match="expected 'lb'"):
        H.parse_metrics([doc(("weight_body_mass", "kg", [{"date": T0, "qty": 100}]))])


def test_a_record_present_in_two_files_counts_once():
    rec = {"date": T0, "qty": 500, "source": ""}
    days, _, _ = H.parse_metrics([doc(("step_count", "count", [rec])),
                                  doc(("step_count", "count", [rec]))])
    assert days["2000-01-02"][0]["steps"] == 500


def test_heart_rate_uses_avg_and_max_fields():
    days, _, _ = H.parse_metrics([doc(("heart_rate", "count/min",
                                       [{"date": T0, "Avg": 60, "Max": 70, "Min": 55},
                                        {"date": T0[:11] + "09:00:00 -0500", "Avg": 80, "Max": 120}]))])
    sums, avgs, hr_max = days["2000-01-02"]
    assert avgs["hr_avg"] == [140.0, 2] and hr_max == 120


def test_workout_activity_matches_the_full_export_names():
    w = {"id": "a", "start": T0, "duration": 600, "source": {"name": "Watch"},
         "activities": [{"activityType": "traditional_strength_training", "metrics": {}}]}
    [(_, row)] = H.parse_workouts([doc(workouts=[w])])
    assert row[2] == "TraditionalStrengthTraining" and row[3] == 10.0


def test_an_icloud_placeholder_is_downloaded_before_it_is_read():
    # 13 Oct 2026: the day's feed file was evicted to a placeholder and the sync died on EDEADLK.
    state = {"dataless": True, "asked": []}
    def download(p):
        state["asked"].append(p); state["dataless"] = False
    H._ensure_local("f.json", is_dataless=lambda p: state["dataless"], download=download,
                    sleep=lambda s: None)
    assert state["asked"] == ["f.json"]


def test_a_placeholder_that_never_arrives_fails_rather_than_being_skipped():
    with pytest.raises(H.NotDownloaded, match="iCloud placeholder"):
        H._ensure_local("f.json", timeout=3, is_dataless=lambda p: True,
                        download=lambda p: None, sleep=lambda s: None)

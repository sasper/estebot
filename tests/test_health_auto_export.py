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


# 1 Nov 2026: json.load of a 52 MB upload took 274 MB and the 512 MB server OOM-killed every
# feed run, so the night's sleep never reached the tables. Files are now streamed and cached.
SLEEP = ("sleep_analysis", "hr", [{"date": T0, "start": "2000-01-02 00:30:00 -0500",
                                   "end": "2000-01-02 07:30:00 -0500", "value": "Core",
                                   "source": "Watch"}])
HRV = ("heart_rate_variability", "ms", [{"date": T0, "qty": 40, "source": "Watch",
                                         "heartbeatSeries": [{"date": T0, "bpm": 60}]}])


def _write(tmp_path, name, d, data_first=False):
    import json
    if data_first:   # the app writes some metrics with "data" before "name"
        d = {"data": {"metrics": [{"data": m["data"], "name": m["name"], "units": m["units"]}
                                  for m in d["data"]["metrics"]]}}
    p = tmp_path / name
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    return p


def test_streaming_a_file_gives_what_parsing_it_whole_gives(tmp_path):
    d = doc(("step_count", "count", [{"date": T0, "qty": 3, "source": "Phone·"}] * 2),
            ("weight_body_mass", "lb", [{"date": T0, "qty": 200.5, "source": "Scale",
                                         "start": T0, "end": T0}]),
            ("physical_effort", "kcal/hr·kg", [{"date": T0, "qty": 1.5}]),
            ("unused_metric", "x", [{"date": T0, "qty": 1, "nested": [{"a": [1, {}]}]}]),
            SLEEP, HRV)
    whole = H.parse_metrics([d])
    for data_first in (False, True):
        p = _write(tmp_path, f"f{data_first}.json", d, data_first)
        st = H._Stream(open(p, encoding="utf-8"), chunk=7)     # refills mid-token constantly
        fp = H.FileParse()
        for k in st.members():
            for k2 in st.members():
                for _ in st.elements():
                    fp._metric(st)
        assert H.combine([fp]) == whole
        assert H.combine([H.FileParse.of_file(p)]) == whole


def test_a_cached_parse_is_reused_until_the_file_changes(tmp_path, monkeypatch):
    rec = {"date": T0, "qty": 500, "source": ""}
    p = _write(tmp_path, "HealthAutoExport-2000-01-02.json", doc(("step_count", "count", [rec])))
    cache = tmp_path / "cache"
    reads = []
    real = H.FileParse.of_file
    monkeypatch.setattr(H.FileParse, "of_file", classmethod(lambda c, q: reads.append(q) or real(q)))
    first = H.combine(H._metric_parses([str(p)], cache))
    assert H.combine(H._metric_parses([str(p)], cache)) == first and len(reads) == 1
    _write(tmp_path, p.name, doc(("step_count", "count", [rec, {**rec, "qty": 7, "date": T0[:11] + "09:00:00 -0500"}])))
    again = H.combine(H._metric_parses([str(p)], cache))
    assert len(reads) == 2 and again[0]["2000-01-02"][0]["steps"] == 507
    H._metric_parses([], cache)                                  # file pruned: its cache goes too
    assert not list(cache.glob("*.pickle"))

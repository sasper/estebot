from estebot.analysis.predictions import parse_ledger

# Line shapes copied from the ledger's own entry format; the claims are made up.
LEDGER = """### P-NNN — <one-line claim>
- **Status:** open | resolved-hit | resolved-miss | resolved-inconclusive | withdrawn
- **Resolves:** YYYY-MM-DD

### P-001 — Body weight trends down at 0.5–1.0 lb/week
- **Status:** **resolved-inconclusive**
- **Resolves:** 2026-09-30

### P-006 — HRV tracks training load
- **Status:** open
- **Resolves:** when three qualifying low-load stretches have occurred, or 2027-03-31,

### P-008 — Adding a fourth weekly session does not change the trend
- **Status:** open (baseline gate amended 2026-10-08, see amendment)
- **Resolves:** 2026-11-30
"""


def test_the_template_entry_is_not_a_prediction():
    assert [p["id"] for p in parse_ledger(LEDGER)] == ["P-001", "P-006", "P-008"]


def test_status_and_resolve_date_as_the_ledger_writes_them():
    p = {x["id"]: x for x in parse_ledger(LEDGER)}
    assert p["P-001"]["status"] == "resolved-inconclusive"          # bold stripped
    assert p["P-006"]["resolves"] == "2027-03-31"                   # the date in the sentence
    assert p["P-008"]["status"].startswith("open")                  # still open, with a note


def test_coverage_counts_the_data_the_prediction_depends_on(con):
    # The demo's HRV prediction reported complete food logs as its data. A coverage with a
    # column counts the days that column has a value: nights with HRV, not daily rows.
    from estebot.analysis import engine, predictions
    con.executemany("INSERT INTO daily (date, hrv_sdnn) VALUES (?, ?)",
                    [("2026-10-01", 44.0), ("2026-10-02", None), ("2026-10-03", 47.0)])
    spec = {"coverage": {"table": "daily", "column": "hrv_sdnn", "window": ["2026-10-01", "2026-10-03"],
                         "need": 2, "label": "nights with HRV"}}
    c = predictions._coverage(con, engine, spec, "2026-10-03")
    assert (c["label"], c["logged"], c["on_track"]) == ("nights with HRV", 2, True)
    import pytest
    with pytest.raises(ValueError):
        engine.coverage(con, "daily", "2026-10-01", "2026-10-03", column="hrv_sdnn; DROP TABLE daily")


def test_the_demo_hrv_prediction_counts_nights_with_hrv(tmp_path):
    import tomllib
    from estebot import demo
    demo.generate(tmp_path / "d", end=demo.DEFAULT_END, days=120)
    specs = tomllib.loads((tmp_path / "d" / "predictions.toml").read_text())
    assert specs["P-003"]["metric"] == "hrv_vs_baseline"
    assert (specs["P-003"]["coverage"]["table"], specs["P-003"]["coverage"]["column"]) == ("daily", "hrv_sdnn")

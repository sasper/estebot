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

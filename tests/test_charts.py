from datetime import date


def test_end_labels_that_would_overlap_are_stacked_apart():
    # A morning weigh-in and its 14-day trend often end within a pound of each other, and
    # their end labels were drawn on top of each other.
    import re
    from estebot.web import charts
    d = [date(2026, 3, i) for i in range(1, 11)]
    svg = charts.line_chart([{"name": "trend", "points": [(x, 180.4) for x in d], "slot": 1},
                             {"name": "weigh-in", "points": [(x, 179.8) for x in d], "slot": 2,
                              "dashed": True}], fmt="{:.0f}")
    ys = sorted(float(y) for y in re.findall(r'class="endlabel[^"]*" x="[^"]*" y="([^"]+)"', svg))
    assert len(ys) == 2 and ys[1] - ys[0] >= 14


def test_an_energy_balance_reads_as_a_deficit_or_a_surplus_never_a_signed_number():
    # The demo showed "Deficit, logged -46": a negative deficit, which is a surplus.
    from estebot.web.charts import kcal_balance
    assert kcal_balance(246.4) == "246 kcal deficit"
    assert kcal_balance(-46) == "46 kcal surplus"
    assert kcal_balance(1234) == "1,234 kcal deficit"
    assert kcal_balance(0.3) == "even" and kcal_balance(None) == "–"

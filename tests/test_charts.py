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

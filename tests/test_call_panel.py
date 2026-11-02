"""Why today's call: plain words in the default view, the engine's numbers behind "Show
the numbers", and rounding that never shows "-0"."""
import re

import pytest

from estecoach import core
from estecoach.web import call_panel as cp

# The default view is for someone who isn't a statistician (asked for on 1 Nov 2026).
BANNED = re.compile(r"\b(RPE|votes?|flagged|pts|kcal|context|earned)\b", re.I)


def test_rounding_for_reading():
    assert cp.signed(-0.4) == "0" and cp.signed(-0.6) == "-1" and cp.signed(29.2) == "+29"
    assert cp.about(2343) == "about 2,340" and cp.one(0.30) == "0.3" and cp.one(7.0) == "7"


def test_effect_lines_round_to_targets_in_ten():
    e = lambda diff: {"diff": diff, "lo": diff - .1, "hi": diff + .1, "n_flagged": 40, "n_clear": 90}
    assert cp.effect(e(-0.30), "poor-sleep") == "On poor-sleep days, you hit about 3 fewer of every 10 targets than usual."
    assert cp.effect(e(0.29), "rough check-in") == "On rough check-in days, you hit about 3 more of every 10 targets than usual."
    assert cp.effect(e(0.04), "unusual-load") == "On unusual-load days, no clear difference in targets hit."
    assert cp.effect({"diff": None, "n_flagged": 3, "n_clear": 90}, "fast-loss") == cp.TOO_FEW
    assert "-0" not in cp.effect_numbers({"diff": -0.10, "lo": -0.21, "hi": -0.004, "n_flagged": 1, "n_clear": 9})
    assert f"{core.EVIDENCE_MIN_N} of each" in cp.effect_numbers({"diff": None, "n_flagged": 1, "n_clear": 9})


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


DEFAULT_KEYS = ("label", "status", "today", "effect", "proven", "word", "when", "shown", "note")


def _default_words(panel):
    d = panel["default"]
    out = [d[k] for k in ("title", "verdict", "summary", "explainer", "example", "safety", "track_text")]
    out += d["holds"]
    for group in ("counted", "watching", "rungs", "reasons", "track"):
        for row in d[group]:
            out += [row[k] for k in DEFAULT_KEYS if isinstance(row.get(k), str)]
    return out


@pytest.fixture(scope="module")
def demo_year(tmp_path_factory):
    from estecoach import config
    import test_call_history as h
    out = h.generate(tmp_path_factory.mktemp("panel"))
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "_SEARCH", [str(out / "estecoach.toml")])
    h.calls(out)                         # ingests, with the modules reloaded on this config
    from estecoach.web import db
    from estecoach.analysis import engine
    yield db.connect(), config.load().goals, engine
    mp.undo()


def test_the_default_view_has_no_jargon_on_any_demo_day(demo_year):
    con, goals, engine = demo_year
    from datetime import date, timedelta
    _, graded = engine._graded(con, goals)
    seen = set()
    day = date(2025, 12, 1)
    while day <= date(2026, 10, 24):
        r = engine.readiness(con, day.isoformat(), goals, graded=graded)
        ev = engine.earned(con, day.isoformat(), goals, graded=graded)["evidence"]
        for present in (False, True):
            words = _default_words(cp.build(r, ev, None, present))
            bad = [w for w in words if BANNED.search(w) or "-0" in w]
            assert not bad, (day, bad)
            if present:
                assert not [w for w in words if re.search(r"spasm|injur|sick", w, re.I)], day
        seen.add(r.get("rule"))
        day += timedelta(days=7)
    assert len(seen) >= 4, seen          # several of the rules, not one day's wording

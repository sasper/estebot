import importlib
from datetime import date

import pytest

from estebot import demo

sync_api = pytest.importorskip("playwright.sync_api")

END = date(2026, 10, 24)


@pytest.fixture(scope="module")
def demo_app(tmp_path_factory):
    # The demo page, served in-process: the browser's requests go to a TestClient, so there
    # is no server to start and no port to pick.
    mp = pytest.MonkeyPatch()
    out = demo.generate(tmp_path_factory.mktemp("demo") / "demo", seed=7, end=END, days=200)
    from estebot import config
    mp.setattr(config, "_SEARCH", [str(out / "estebot.toml")])
    mp.setenv("ESTEBOT_DEMO", "1")
    from estebot.web import db, ingest
    importlib.reload(db)
    importlib.reload(ingest)
    from estebot.analysis import engine
    importlib.reload(engine)
    ingest.run()
    from estebot.web import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    yield TestClient(main.app)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:           # the package without its browser
            pytest.skip(f"no Chromium for Playwright: {e}")
        yield b
        b.close()


def _serve(client):
    def handle(route):
        url = route.request.url
        if not url.startswith("http://estebot.test/"):
            return route.abort()         # web fonts: the fallback font is wider, so a stricter test
        r = client.get(url.removeprefix("http://estebot.test"))
        route.fulfill(status=r.status_code, body=r.content,
                      headers={"content-type": r.headers.get("content-type", "text/html")})
    return handle


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_demo_fits_a_phone_with_every_tile_open(demo_app, browser, scheme):
    # At 390px the pace rows, the nutrient tables and the Ask button pushed the page to 501px,
    # so it scrolled sideways.
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme=scheme)
    page.route("**/*", _serve(demo_app))
    page.goto(f"http://estebot.test/?day={END.isoformat()}")
    tiles = page.locator("details.tile")
    assert tiles.count() >= 5
    page.evaluate("document.querySelectorAll('details.tile').forEach(d => d.open = true)")
    width = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    # What sticks out, leaving out what sits inside its own scroller (the intro cards)
    wide = page.evaluate("""() => [...document.querySelectorAll('body *')]
        .filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 0.5)
        .filter(e => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement)
                         if (getComputedStyle(a).overflowX !== 'visible') return false;
                       return true; })
        .map(e => e.tagName.toLowerCase() + (e.className ? '.' + e.className : ''))""")
    page.close()
    assert width[0] <= width[1], wide

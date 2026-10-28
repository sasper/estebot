from pathlib import Path

from estebot.web import service


def test_the_page_serves_merged_code_and_comes_back_by_itself():
    # 12-15 Oct: a hand-started server kept showing the page as it was on 12 Oct through
    # several merged PRs, because uvicorn loads code once. The service runs from the
    # sync's origin/main checkout and launchd keeps it up.
    runner, main = Path("/x/runner"), Path("/x/estebot")
    p = service.plist(runner, main, "/x/estebot/.venv/bin/python")
    assert p["WorkingDirectory"] == str(runner)
    assert p["EnvironmentVariables"]["PYTHONPATH"] == "/x/runner/src"
    assert p["EnvironmentVariables"]["ESTEBOT_CONFIG"] == "/x/estebot/estebot.toml"
    assert p["KeepAlive"] and p["RunAtLoad"]
    assert p["ProgramArguments"][-1] == "8742"


def test_the_update_check_restarts_only_on_new_code_and_never_mid_sync():
    # 16 Oct: both PRs merged at 12:21, minutes after the noon restart, so the page kept the
    # old code for a day. The check runs every 15 minutes but must not move the checkout
    # while the daily sync is reading it.
    sh = service.update_script(Path("/x/runner"), 501)
    assert sh.index("rev-parse origin/main") < sh.index("checkout -q --detach origin/main")
    assert sh.index("com.estebot.daily") < sh.index("checkout -q --detach origin/main")
    assert "kickstart -k gui/501/com.estebot.web" in sh
    p = service.update_plist(Path("/x/runner"), 501)
    assert p["StartInterval"] == 900 and p["ProgramArguments"][:2] == ["/bin/sh", "-c"]

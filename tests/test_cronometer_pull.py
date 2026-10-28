import pytest

from estebot.parsers import cronometer_pull as C


def test_the_login_token_is_read_from_the_form():
    assert C.anticsrf('<input type="hidden" name="anticsrf" value="abc123"/>') == "abc123"


def test_a_refused_login_fails_with_the_sites_reason():
    # The site answers 200 with an error field, so a status check alone would pass it.
    with pytest.raises(C.PullError, match="AntiCSRF Token Invalid"):
        C.check_login('{"error": "AntiCSRF Token Invalid"}')


def test_the_rpc_policy_id_is_read_from_the_app_build():
    # Read on every run: the build id changed between 13 and 14 Oct 2026.
    js = "H7j.call(this,KWk(),'app','9AE2A14D962F8D53422A5AF8355930C3',uak)"
    assert C.gwt_policy(js) == "9AE2A14D962F8D53422A5AF8355930C3"


def test_an_rpc_exception_names_its_class():
    # 14 Oct 2026: reading sesnonce before authenticate rotated it gave exactly this answer.
    body = ('//EX[2,1,["com.cronometer.shared.user.exceptions.NotLoggedInException/844385496",'
            '"Invalid or expired session"],0,7]')
    with pytest.raises(C.PullError, match="NotLoggedInException"):
        C.gwt_ok(body, r'"([0-9a-f]{32})"', "generateAuthorizationToken")

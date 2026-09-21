"""Tests for local authentication endpoints and logout flow."""

from falcon import testing

from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
from notoons.app.main import create_app


def test_logout_clears_auth_cookies_without_starting_login():
    """Verify that logging out clears local cookies, renders signed-out page, and does not start login."""
    client = testing.TestClient(create_app())
    response = client.simulate_get(
        "/auth/logout",
        headers={"Cookie": f"{AUTH_SESSION_COOKIE}=session"},
    )
    assert response.status_code == 200
    assert "location" not in response.headers
    assert "You have been signed out." in response.text
    set_cookie = response.headers["set-cookie"]
    assert f'{AUTH_SESSION_COOKIE}=""' in set_cookie


def test_unauthenticated_access_is_blocked():
    """Verify that protected routes require authentication and cannot be accessed after logout."""
    client = testing.TestClient(create_app())

    # Main page redirects to /auth/login
    res_home = client.simulate_get("/")
    assert res_home.status_code == 302
    assert "/auth/login" in res_home.headers.get("location", "")

    # Protected API endpoints return 401 Unauthorized
    res_jobs = client.simulate_get("/jobs")
    assert res_jobs.status_code == 401

    res_config = client.simulate_get("/config")
    assert res_config.status_code == 401

    res_me = client.simulate_get("/auth/me")
    assert res_me.status_code == 401


def test_logout_callback_redirects_to_logout_page():
    """Verify that post-logout callback redirects to /auth/logout."""
    client = testing.TestClient(create_app())
    response = client.simulate_get("/auth/callback?state=logged_out")
    assert response.status_code == 302
    assert response.headers.get("location") == "/auth/logout"


if __name__ == "__main__":
    test_logout_clears_auth_cookies_without_starting_login()
    test_unauthenticated_access_is_blocked()
    test_logout_callback_redirects_to_logout_page()
    print("ALL AUTH & LOGOUT TESTS PASSED!")

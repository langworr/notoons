"""Falcon routes for OpenID Connect login and logout."""

import falcon.asgi
import httpx

from ...auth import OIDCClient, OIDCError, new_login_transaction


AUTH_TRANSACTION_COOKIE = "notoons_oidc_tx"
AUTH_SESSION_COOKIE = "notoons_session"


class AuthResource:
    """Start login, complete the OIDC callback, and end the local session."""

    def __init__(self, oidc: OIDCClient) -> None:
        self.oidc = oidc

    async def on_get(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Redirect the browser to the configured OIDC provider."""
        try:
            transaction = new_login_transaction()
            next_url = req.get_param("next") or "/"
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = "/"
            transaction["next"] = next_url
            location = await self.oidc.authorization_url(
                transaction["state"], transaction["nonce"], transaction["challenge"]
            )
            resp.set_cookie(
                AUTH_TRANSACTION_COOKIE,
                self.oidc.make_cookie(transaction, 600),
                max_age=600,
                path="/auth",
                secure=self.oidc.cookie_secure,
                http_only=True,
                same_site="Lax",
            )
            resp.status = falcon.HTTP_302
            resp.location = location
        except (OIDCError, KeyError, OSError, httpx.HTTPError) as exc:
            resp.status = falcon.HTTP_503
            resp.text = str(exc)

    async def on_get_callback(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Validate the provider callback and establish a local session."""
        if req.get_param("state") == "logged_out" or (
            not req.get_param("code") and not req.cookies.get(AUTH_TRANSACTION_COOKIE)
        ):
            resp.unset_cookie(AUTH_SESSION_COOKIE, path="/")
            resp.unset_cookie(AUTH_TRANSACTION_COOKIE, path="/auth")
            resp.status = falcon.HTTP_302
            resp.location = "/auth/logout"
            return

        try:
            error = req.get_param("error")
            if error:
                raise OIDCError(f"OIDC provider returned an error: {error}")
            transaction_cookie = req.cookies.get(AUTH_TRANSACTION_COOKIE)
            if not transaction_cookie:
                raise OIDCError("OIDC login transaction is missing or expired.")
            transaction = self.oidc.read_cookie(transaction_cookie)
            if req.get_param("state") != transaction.get("state"):
                raise OIDCError("OIDC state validation failed.")
            code = req.get_param("code")
            if not code:
                raise OIDCError("OIDC callback did not contain an authorization code.")
            result = await self.oidc.exchange_code(
                code, str(transaction["nonce"]), str(transaction["verifier"])
            )
            claims = result["claims"]
            session = {
                "sub": claims.get("sub"),
                "name": claims.get("name") or claims.get("preferred_username"),
                "email": claims.get("email"),
                "id_token": result.get("id_token"),
            }
            resp.set_cookie(
                AUTH_SESSION_COOKIE,
                self.oidc.make_cookie(session, 8 * 60 * 60),
                max_age=8 * 60 * 60,
                path="/",
                secure=self.oidc.cookie_secure,
                http_only=True,
                same_site="Lax",
            )
            resp.unset_cookie(AUTH_TRANSACTION_COOKIE, path="/auth")
            resp.status = falcon.HTTP_302
            resp.location = str(transaction.get("next", "/"))
        except (OIDCError, KeyError, TypeError, ValueError, httpx.HTTPError) as exc:
            resp.status = falcon.HTTP_401
            resp.text = str(exc)

    async def on_get_logout(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Clear the local session cookie and render the signed-out confirmation."""
        id_token_hint = None
        cookie = req.cookies.get(AUTH_SESSION_COOKIE)
        if cookie:
            try:
                user_session = self.oidc.read_cookie(cookie)
                id_token_hint = user_session.get("id_token")
            except OIDCError:
                pass

        resp.unset_cookie(AUTH_SESSION_COOKIE, path="/")

        if req.get_param("sso") in ("1", "true", "yes"):
            logout_url = await self.oidc.logout_url(
                id_token_hint=id_token_hint, state="logged_out"
            )
            if logout_url:
                resp.status = falcon.HTTP_302
                resp.location = logout_url
                return

        sso_logout_url = None
        if self.oidc.configured:
            try:
                sso_logout_url = await self.oidc.logout_url(
                    id_token_hint=id_token_hint, state="logged_out"
                )
            except OIDCError:
                sso_logout_url = None

        resp.status = falcon.HTTP_200
        resp.content_type = falcon.MEDIA_HTML
        resp.text = self._render_logged_out_html(sso_logout_url)

    @staticmethod
    def _render_logged_out_html(sso_logout_url: str | None = None) -> str:
        sso_button = ""
        if sso_logout_url:
            sso_button = f"""
            <a href="{sso_logout_url}" style="display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; \
                padding: 0.65rem 1.25rem; background: rgba(248, 113, 113, 0.12); border: 1px solid rgba(248, 113, 113, 0.3); \
                border-radius: 8px; color: #fca5a5; font-size: 0.88rem; font-weight: 600; text-decoration: none; transition: all 0.2s;">
              <svg style="width: 16px; height: 16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" \
                    d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
              </svg>
              Sign out of SSO
            </a>
            """

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" type="image/png" href="/static/images/favicon.png">
  <title>Signed Out - Notoons</title>
  <link rel="stylesheet" href="/static/css/styles.css">
  <style>
    body {{
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      background: #0b0f19;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    .logged-out-card {{
      background: #111827;
      border: 1px solid #1e293b;
      border-radius: 16px;
      padding: 2.5rem;
      max-width: 440px;
      width: 90%;
      text-align: center;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
    }}
    .icon-wrap {{
      width: 56px;
      height: 56px;
      background: rgba(56, 189, 248, 0.1);
      border: 1px solid rgba(56, 189, 248, 0.25);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 1.5rem auto;
      color: #38bdf8;
    }}
    .title {{
      font-size: 1.4rem;
      font-weight: 700;
      color: #ffffff;
      margin: 0 0 0.5rem 0;
    }}
    .subtitle {{
      color: #94a3b8;
      font-size: 0.9rem;
      line-height: 1.5;
      margin: 0 0 2rem 0;
    }}
    .actions {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    .btn-signin {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      padding: 0.75rem 1.25rem;
      background: linear-gradient(135deg, #0284c7 0%, #2563eb 100%);
      color: #ffffff;
      font-size: 0.9rem;
      font-weight: 600;
      border-radius: 8px;
      text-decoration: none;
      transition: all 0.2s;
    }}
    .btn-signin:hover {{
      opacity: 0.95;
      transform: translateY(-1px);
    }}
  </style>
</head>
<body>
  <div class="logged-out-card">
    <div class="icon-wrap">
      <svg style="width: 28px; height: 28px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
      </svg>
    </div>
    <h1 class="title">You have been signed out.</h1>
    <p class="subtitle">Your local session has ended and the application is no longer active.</p>
    <div class="actions">
      <a href="/auth/login" class="btn-signin">
        <svg style="width: 18px; height: 18px;" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" \
            d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1"/>
        </svg>
        Sign in again
      </a>
      {sso_button}
    </div>
  </div>
</body>
</html>"""

    async def on_get_me(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Return the authenticated user's basic claims as JSON."""
        try:
            cookie = req.cookies.get(AUTH_SESSION_COOKIE)
            if not cookie:
                raise OIDCError("Authentication required.")
            user = self.oidc.read_cookie(cookie)
        except OIDCError as exc:
            resp.status = falcon.HTTP_401
            resp.media = {"error": str(exc)}
            return
        resp.media = {"user": user}
        resp.status = falcon.HTTP_200

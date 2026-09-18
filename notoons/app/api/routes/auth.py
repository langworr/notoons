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

    async def on_get_logout(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Clear the local session cookie and redirect to the login page."""
        resp.unset_cookie(AUTH_SESSION_COOKIE, path="/")
        resp.status = falcon.HTTP_302
        resp.location = "/auth/login"

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
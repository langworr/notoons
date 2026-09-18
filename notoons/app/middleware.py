"""Authentication middleware for the protected Notoons application."""

import falcon
import falcon.asgi

from .api.routes.auth import AUTH_SESSION_COOKIE
from .auth import OIDCClient, OIDCError


class AuthenticationMiddleware:
    """Require a valid OIDC session for application and API routes."""

    def __init__(self, oidc: OIDCClient) -> None:
        self.oidc = oidc

    async def process_request(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Load the session or redirect/return 401 before route handling."""
        if req.path.startswith("/auth/") or req.path.startswith("/static/"):
            return
        try:
            cookie = req.cookies.get(AUTH_SESSION_COOKIE)
            if cookie:
                req.context.user = self.oidc.read_cookie(cookie)
                return
        except OIDCError:
            pass
        if req.path.startswith(("/jobs", "/process", "/download", "/config")):
            raise falcon.HTTPUnauthorized(
                title="Authentication required",
                description="Sign in at /auth/login before using this endpoint.",
            )
        raise falcon.HTTPFound(location=f"/auth/login?next={req.path}")
"""OpenID Connect configuration, token validation, and cookie sessions."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from .config import CONFIG


class OIDCError(Exception):
    """Raised when OIDC configuration or an OIDC response is invalid."""


class OIDCClient:
    """Small provider-agnostic OpenID Connect authorization-code client.

    Provider settings are read from :data:`notoons.app.config.CONFIG`, which
    loads ``config.txt`` and applies environment-variable overrides.  The
    client uses discovery, PKCE, nonce validation, and JWKS-backed ID-token
    validation.
    """

    def __init__(self) -> None:
        self.issuer = CONFIG["oidc_issuer_url"].rstrip("/")
        self.client_id = CONFIG["oidc_client_id"]
        self.client_secret = CONFIG["oidc_client_secret"]
        self.redirect_uri = CONFIG["oidc_redirect_uri"]
        self.scopes = CONFIG["oidc_scopes"]
        self.session_secret = CONFIG["oidc_session_secret"]
        self.cookie_secure = CONFIG["oidc_cookie_secure"].lower() == "true"
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        """Return whether the minimum OIDC settings are available."""
        return bool(self.issuer and self.client_id and self.session_secret)

    def require_configured(self) -> None:
        """Raise an explicit error when OIDC has not been configured."""
        if not self.configured:
            raise OIDCError(
                "Set OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_SESSION_SECRET."
            )

    async def discovery(self) -> dict[str, Any]:
        """Fetch and cache the provider's OpenID Connect discovery document."""
        self.require_configured()
        if self._discovery is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.issuer}/.well-known/openid-configuration"
                )
                response.raise_for_status()
                self._discovery = response.json()
        return self._discovery

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        """Encode a JSON mapping into a URL-safe, unpadded base64 string."""
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        """Decode a URL-safe base64 JSON mapping or raise ``OIDCError``."""
        try:
            padding = "=" * (-len(value) % 4)
            data = base64.urlsafe_b64decode(value + padding)
            decoded = json.loads(data)
            if not isinstance(decoded, dict):
                raise ValueError
            return decoded
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OIDCError("Invalid OIDC cookie payload.") from exc

    def sign(self, value: str) -> str:
        """Sign a cookie payload with the configured HMAC secret."""
        signature = hmac.new(
            self.session_secret.encode(), value.encode(), hashlib.sha256
        ).hexdigest()
        return f"{value}.{signature}"

    def unsign(self, value: str) -> str:
        """Verify and return a signed cookie payload."""
        try:
            payload, signature = value.rsplit(".", 1)
        except ValueError as exc:
            raise OIDCError("Invalid OIDC cookie signature.") from exc
        expected = hmac.new(
            self.session_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise OIDCError("Invalid OIDC cookie signature.")
        return payload

    def make_cookie(self, data: dict[str, Any], expires_in: int) -> str:
        """Create a signed cookie value with an expiry timestamp."""
        payload = dict(data)
        payload["exp"] = int(time.time()) + expires_in
        return self.sign(self._encode(payload))

    def read_cookie(self, value: str) -> dict[str, Any]:
        """Verify, decode, and expiry-check a signed cookie value."""
        payload = self._decode(self.unsign(value))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise OIDCError("OIDC cookie expired.")
        return payload

    async def authorization_url(self, state: str, nonce: str, challenge: str) -> str:
        """Build the provider authorization URL for a login transaction."""
        discovery = await self.discovery()
        redirect_uri = self.redirect_uri or ""
        if not redirect_uri:
            raise OIDCError("Set OIDC_REDIRECT_URI to the callback URL.")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, code: str, nonce: str, verifier: str) -> dict[str, Any]:
        """Exchange an authorization code and validate its returned ID token."""
        discovery = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        auth = (self.client_id, self.client_secret) if self.client_secret else None
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(discovery["token_endpoint"], data=data, auth=auth)
            response.raise_for_status()
            token_response = response.json()

        id_token = token_response.get("id_token")
        if not id_token:
            raise OIDCError("OIDC provider did not return an ID token.")
        claims = await self.validate_id_token(id_token, nonce)
        return {"claims": claims, "access_token": token_response.get("access_token")}

    async def validate_id_token(self, token: str, nonce: str) -> dict[str, Any]:
        """Validate an ID token's signature, issuer, audience, and nonce."""
        discovery = await self.discovery()
        if self._jwks is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(discovery["jwks_uri"])
                response.raise_for_status()
                self._jwks = response.json()
        header = jwt.get_unverified_header(token)
        key_data = next(
            (key for key in self._jwks.get("keys", []) if key.get("kid") == header.get("kid")),
            None,
        )
        if not key_data:
            raise OIDCError("No matching OIDC signing key was found.")
        try:
            key = jwt.PyJWK.from_dict(key_data).key
            claims = jwt.decode(
                token,
                key,
                algorithms=[header.get("alg", "")],
                audience=self.client_id,
                issuer=self.issuer,
            )
        except jwt.PyJWTError as exc:
            raise OIDCError("ID-token validation failed.") from exc
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("OIDC nonce validation failed.")
        return claims


def new_login_transaction() -> dict[str, str]:
    """Create state, nonce, and PKCE verifier values for one login attempt."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": verifier,
        "challenge": challenge,
    }
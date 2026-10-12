"""Google OAuth2, JWT (HS256/RS256), and token verification infrastructure adapter."""

import contextlib
import logging
from typing import Any

import jwt
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.providers.jwt import JWTVerifier
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from sector5_mcp.config import settings
from sector5_mcp.domain.exceptions.auth import AuthenticationError
from sector5_mcp.domain.interfaces.auth import AuthVerifierInterface
from sector5_mcp.domain.models.auth import AuthIdentity

logger = logging.getLogger("sector5_mcp.auth_adapter")


class GoogleJWTVerifier(JWTVerifier):
    """FastMCP JWTVerifier validating Google ID tokens and restricting by allowed emails."""

    def __init__(
        self,
        allowed_emails: list[str] | None = None,
        jwks_uri: str = "https://www.googleapis.com/oauth2/v3/certs",
        issuer: str = "https://accounts.google.com",
        **kwargs,
    ):
        super().__init__(jwks_uri=jwks_uri, issuer=issuer, **kwargs)
        self.allowed_emails = set(allowed_emails or [])

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = await super().load_access_token(token)
        if not access_token:
            return None

        if self.allowed_emails:
            email = access_token.claims.get("email")
            if not email or email not in self.allowed_emails:
                logger.warning(
                    f"Google ID token rejected: email '{email}' is not in allowed_google_emails."
                )
                return None

        return access_token


class GoogleAuthVerifier(AuthVerifierInterface):
    def _get_candidate_hs256_secrets(self) -> list[str | bytes]:
        """Collect all candidate symmetric secrets for HS256 JWT signature verification."""
        candidate_secrets: list[str | bytes] = []
        raw_secrets: list[str] = []

        if settings.jwt_secret:
            raw_secrets.append(settings.jwt_secret)
        if settings.google_client_secret:
            raw_secrets.append(settings.google_client_secret)
        if settings.service_token:
            raw_secrets.append(settings.service_token)

        for secret in raw_secrets:
            candidate_secrets.append(secret)
            with contextlib.suppress(Exception):
                candidate_secrets.append(
                    derive_jwt_key(high_entropy_material=secret, salt="fastmcp-jwt-signing-key")
                )
            with contextlib.suppress(Exception):
                candidate_secrets.append(
                    derive_jwt_key(low_entropy_material=secret, salt="fastmcp-jwt-signing-key")
                )

        return candidate_secrets

    def _verify_hs256_jwt(self, token: str) -> AuthIdentity:
        """Verify an HS256-signed JWT token against configured secrets."""
        candidate_secrets = self._get_candidate_hs256_secrets()
        if not candidate_secrets:
            logger.warning(
                "HS256 JWT received but no jwt_secret, google_client_secret, or service_token configured."
            )
            raise AuthenticationError(
                "Unsupported signature algorithm HS256: No HMAC secret key configured."
            )

        payload: dict[str, Any] | None = None
        last_error: Exception | None = None

        for secret in candidate_secrets:
            try:
                payload = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256"],
                    options={"verify_signature": True, "verify_exp": True, "verify_aud": False},
                )
                break
            except jwt.ExpiredSignatureError as exc:
                logger.warning(f"HS256 JWT token expired: {exc}")
                raise AuthenticationError(
                    f"Authentication failed: Token has expired ({exc})"
                ) from exc
            except jwt.InvalidTokenError as exc:
                last_error = exc
                continue

        if payload is None:
            logger.warning(f"HS256 JWT signature verification failed: {last_error}")
            raise AuthenticationError(
                f"Authentication failed: Invalid HS256 signature ({last_error})"
            )

        # Extract identity claims from payload (including FastMCP upstream_claims structure)
        upstream = (
            payload.get("upstream_claims")
            if isinstance(payload.get("upstream_claims"), dict)
            else {}
        )
        email = (
            payload.get("email")
            or upstream.get("email")
            or (
                payload.get("client_id")
                if isinstance(payload.get("client_id"), str) and "@" in payload.get("client_id")
                else None
            )
        )
        name = payload.get("name") or upstream.get("name")
        user = (
            email
            or payload.get("user")
            or payload.get("sub")
            or payload.get("client_id")
            or "jwt-client"
        )

        allowed_emails = (
            settings.allowed_google_emails
            if isinstance(settings.allowed_google_emails, list)
            else [settings.allowed_google_emails]
        )
        if allowed_emails and email and email not in allowed_emails:
            raise AuthenticationError(
                f"Access denied: User '{email}' is not in the allowed emails list."
            )

        return AuthIdentity(
            authenticated=True,
            user=user,
            auth_type="jwt_hs256",
            name=name,
        )

    def verify(self, token: str | None) -> AuthIdentity:
        if not settings.auth_enabled:
            return AuthIdentity(authenticated=True, user="anonymous", auth_type="disabled")

        if not token:
            raise AuthenticationError("Authentication required: No token provided.")

        # 1. Internal static service token check
        if settings.service_token and token == settings.service_token:
            return AuthIdentity(
                authenticated=True, user="service-account", auth_type="service_token"
            )

        # 2. Check if token is a JWT and inspect algorithm
        is_hs256 = False
        try:
            unverified_header = jwt.get_unverified_header(token)
            if unverified_header.get("alg") == "HS256":
                is_hs256 = True
        except Exception:
            # Not a valid JWT header format; fall back to Google or static verification
            pass

        if is_hs256:
            return self._verify_hs256_jwt(token)

        # 3. Google OAuth2 ID token verification (RS256 / ES256)
        try:
            request = google_requests.Request()
            id_info = id_token.verify_oauth2_token(
                token,
                request,
                audience=settings.google_client_id if settings.google_client_id else None,
            )

            email = id_info.get("email")
            if not email:
                raise AuthenticationError("Invalid Google token: email claim missing.")

            allowed_emails = (
                settings.allowed_google_emails
                if isinstance(settings.allowed_google_emails, list)
                else [settings.allowed_google_emails]
            )
            if allowed_emails and email not in allowed_emails:
                raise AuthenticationError(
                    f"Access denied: Google user '{email}' is not in the allowed emails list."
                )

            return AuthIdentity(
                authenticated=True,
                user=email,
                auth_type="google_oauth",
                name=id_info.get("name"),
            )
        except AuthenticationError:
            raise
        except Exception as exc:
            logger.warning(f"Google token verification failed: {exc}")
            raise AuthenticationError(f"Authentication failed: {exc}") from exc

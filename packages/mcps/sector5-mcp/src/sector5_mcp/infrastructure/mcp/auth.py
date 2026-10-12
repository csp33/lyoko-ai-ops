"""FastMCP authentication provider wiring for the gateway server."""

import logging
from typing import Any

from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier
from fastmcp.server.auth.oidc_proxy import OIDCProxy

from sector5_mcp.application.service import MCPGatewayService
from sector5_mcp.config import settings
from sector5_mcp.infrastructure.auth.google import GoogleJWTVerifier

logger = logging.getLogger("sector5_mcp.gateway_server")


class GatewayTokenVerifier(TokenVerifier):
    """FastMCP TokenVerifier bridging FastMCP auth to Domain AuthVerifier."""

    def __init__(self, service: MCPGatewayService):
        super().__init__()
        self.service = service

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            identity = self.service.verify_access(token)
            if identity.authenticated:
                return AccessToken(
                    token=token,
                    client_id=identity.user,
                    scopes=[],
                    expires_at=None,
                )
        except Exception as exc:
            logger.warning(f"FastMCP authentication rejected: {exc}")
            return None
        return None


def build_auth_provider(service: MCPGatewayService) -> Any | None:
    """Build the gateway auth provider from settings, or None when auth is disabled."""
    if not settings.auth_enabled:
        return None

    token_verifier = GatewayTokenVerifier(service)
    if not (settings.google_client_id and settings.google_client_secret):
        return token_verifier

    allowed_emails = (
        settings.allowed_google_emails
        if isinstance(settings.allowed_google_emails, list)
        else [settings.allowed_google_emails]
    )
    google_verifier = GoogleJWTVerifier(
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        audience=settings.google_client_id,
        allowed_emails=allowed_emails,
    )
    oidc_proxy = OIDCProxy(
        config_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        jwt_signing_key=settings.google_client_secret,
        base_url=settings.base_url,
        redirect_path=settings.redirect_path,
        verify_id_token=True,
        valid_scopes=["openid", "email", "profile"],
        extra_authorize_params={"scope": "openid email profile"},
        token_verifier=google_verifier,
    )
    return MultiAuth(server=oidc_proxy, verifiers=[token_verifier])

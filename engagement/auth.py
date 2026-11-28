"""Vercel OIDC authentication without a long-lived shared service secret."""

from __future__ import annotations

from functools import lru_cache
import os
from urllib.parse import urlparse

from .models import EngagementError


class AuthenticationError(EngagementError):
    pass


def expected_subject(team_slug: str, project_name: str, environment: str) -> str:
    if not team_slug or not project_name:
        raise AuthenticationError("Vercel team and project must be configured")
    if environment != "production":
        raise AuthenticationError("only the production Morph workload may launch engagements")
    return f"owner:{team_slug}:project:{project_name}:environment:{environment}"


@lru_cache(maxsize=8)
def _jwk_client(issuer: str):
    try:
        import jwt
    except ImportError as exc:
        raise AuthenticationError("OIDC verification is unavailable in this runtime") from exc
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks")


class VercelOidcVerifier:
    def __init__(self, issuer: str, audience: str, subject: str):
        for name, value in (("issuer", issuer), ("audience", audience), ("subject", subject)):
            if not value:
                raise AuthenticationError(f"OIDC {name} is not configured")
        parsed_issuer = urlparse(issuer)
        if (issuer != issuer.rstrip("/") or parsed_issuer.scheme != "https"
                or parsed_issuer.hostname != "oidc.vercel.com" or parsed_issuer.query
                or parsed_issuer.fragment or parsed_issuer.username or parsed_issuer.password):
            raise AuthenticationError("OIDC issuer must be Vercel's team HTTPS issuer")
        self.issuer = issuer
        self.audience = audience
        self.subject = subject

    @classmethod
    def from_environment(cls, environ=None) -> "VercelOidcVerifier":
        env = os.environ if environ is None else environ
        team_slug = env.get("VERCEL_TEAM_SLUG", "")
        return cls(
            issuer=env.get("VERCEL_OIDC_ISSUER", f"https://oidc.vercel.com/{team_slug}"),
            audience=env.get("PENHARNESS_OIDC_AUDIENCE", ""),
            subject=expected_subject(
                team_slug,
                env.get("VERCEL_PROJECT_NAME", ""),
                env.get("VERCEL_ENVIRONMENT", "production"),
            ),
        )

    def verify(self, authorization: str) -> dict:
        if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
            raise AuthenticationError("missing bearer token")
        token = authorization.removeprefix("Bearer ")
        try:
            import jwt
            key = _jwk_client(self.issuer).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                subject=self.subject,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError("invalid workload identity") from exc
        if claims.get("environment") and claims["environment"] != "production":
            raise AuthenticationError("invalid workload environment")
        return claims

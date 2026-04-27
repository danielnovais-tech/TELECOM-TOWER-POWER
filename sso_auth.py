"""
sso_auth.py — OIDC token verification for SSO-issued API keys.

Today: AWS Cognito (User Pool) ID-tokens. Tomorrow: any OIDC IdP that
exposes a JWKS endpoint — just plug the issuer and audience into
``IDP_REGISTRY``. Pure-stdlib + PyJWT[crypto] + ``requests`` (already a
direct dep). NO httpx, NO authlib.

Design contract:
  * Signature, expiry, issuer, audience, and ``token_use=id`` are all
    cryptographically verified before any database lookup happens.
  * JWKS is fetched once and cached for ``JWKS_TTL_S`` (15 min). On a
    KID miss we refresh once before failing — covers IdP key rotation.
  * Verification errors raise ``SsoTokenError`` with a generic message;
    the caller maps that to HTTP 401. We log the underlying reason at
    WARNING but never echo it to the client (avoids oracle attacks).
  * No dependency on FastAPI / async loop — safe to call from sync code.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("sso_auth")


class SsoTokenError(Exception):
    """Raised when an Authorization: Bearer token fails verification."""


# ─── Lazy imports (PyJWT[crypto] + requests) ───────────────────────────
# Import inside helpers so a missing dep never breaks API import; the
# /auth/sso endpoint surfaces a clean 503 instead.

def _import_jwt():
    try:
        import jwt  # type: ignore[import-untyped]
        from jwt import algorithms as _alg  # noqa: F401
    except ImportError as exc:
        raise SsoTokenError(f"PyJWT not installed: {exc}") from exc
    return jwt


def _import_requests():
    try:
        import requests  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SsoTokenError(f"requests not installed: {exc}") from exc
    return requests


# ─── IdP registry (env-driven) ─────────────────────────────────────────

@dataclass(frozen=True)
class IdpConfig:
    name: str
    issuer: str
    jwks_url: str
    audience: str
    # Cognito ID-tokens carry "token_use": "id"; "access" tokens are not
    # acceptable here because Cognito access tokens omit ``aud``.
    require_token_use: Optional[str] = "id"


def _cognito_config() -> Optional[IdpConfig]:
    region = os.getenv("COGNITO_REGION", "sa-east-1").strip()
    pool = os.getenv("COGNITO_USER_POOL_ID", "").strip()
    client = os.getenv("COGNITO_APP_CLIENT_ID", "").strip()
    if not pool or not client:
        return None
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"
    return IdpConfig(
        name="cognito",
        issuer=issuer,
        jwks_url=f"{issuer}/.well-known/jwks.json",
        audience=client,
    )


def get_idp(provider: str = "cognito") -> Optional[IdpConfig]:
    if provider == "cognito":
        return _cognito_config()
    return None


def is_sso_configured() -> bool:
    return _cognito_config() is not None


# ─── JWKS cache ─────────────────────────────────────────────────────────

_JWKS_TTL_S = int(os.getenv("SSO_JWKS_TTL_S", "900"))  # 15 minutes
_JWKS_HTTP_TIMEOUT_S = float(os.getenv("SSO_JWKS_HTTP_TIMEOUT_S", "5"))
_jwks_lock = threading.Lock()
_jwks_cache: Dict[str, Dict[str, Any]] = {}  # keyed by jwks_url


def _fetch_jwks(jwks_url: str, *, force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force:
        cached = _jwks_cache.get(jwks_url)
        if cached and now - cached.get("_fetched_at", 0) < _JWKS_TTL_S:
            return cached["data"]
    requests = _import_requests()
    resp = requests.get(jwks_url, timeout=_JWKS_HTTP_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    with _jwks_lock:
        _jwks_cache[jwks_url] = {"data": data, "_fetched_at": now}
    return data


def _key_for_kid(jwks_url: str, kid: str):
    """Return the ``cryptography`` public-key object for ``kid`` (or raise)."""
    jwt = _import_jwt()
    for attempt in range(2):  # retry once with forced refresh on KID miss
        jwks = _fetch_jwks(jwks_url, force=(attempt == 1))
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                import json
                return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    raise SsoTokenError("no matching JWKS key for kid")


# ─── Public API ─────────────────────────────────────────────────────────

def verify_id_token(token: str, *, provider: str = "cognito") -> Dict[str, Any]:
    """Verify ``token`` and return its claims dict.

    Raises :class:`SsoTokenError` on any failure (signature, expiry,
    audience, issuer, token_use, missing config). Never returns partial
    or unverified claims.
    """
    if not token or not isinstance(token, str):
        raise SsoTokenError("empty token")
    cfg = get_idp(provider)
    if cfg is None:
        raise SsoTokenError(f"SSO provider '{provider}' not configured")
    jwt = _import_jwt()
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise SsoTokenError("malformed token header") from exc
    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or alg not in ("RS256", "RS384", "RS512"):
        raise SsoTokenError("unsupported token algorithm")
    try:
        key = _key_for_kid(cfg.jwks_url, kid)
    except SsoTokenError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("jwks fetch failed for %s: %s", cfg.jwks_url, exc)
        raise SsoTokenError("jwks unavailable") from exc
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=[alg],
            audience=cfg.audience,
            issuer=cfg.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise SsoTokenError("token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise SsoTokenError("invalid audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise SsoTokenError("invalid issuer") from exc
    except jwt.InvalidTokenError as exc:
        raise SsoTokenError("invalid token") from exc
    if cfg.require_token_use:
        if claims.get("token_use") != cfg.require_token_use:
            raise SsoTokenError("wrong token_use")
    if not claims.get("sub"):
        raise SsoTokenError("missing sub claim")
    return claims


def reset_jwks_cache_for_tests() -> None:
    with _jwks_lock:
        _jwks_cache.clear()


# ─── OAuth2 Authorization Code exchange (server-side, keeps secret off SPA) ──

def _cognito_oauth_endpoints() -> Optional[Dict[str, str]]:
    """Resolve the Hosted-UI base URL + client secret for code exchange.

    Requires env: COGNITO_DOMAIN (host only, e.g.
    ``sa-east-115ur6sr9o.auth.sa-east-1.amazoncognito.com``) and
    COGNITO_APP_CLIENT_SECRET. Returns None if either is missing.
    """
    domain = os.getenv("COGNITO_DOMAIN", "").strip().rstrip("/")
    client_id = os.getenv("COGNITO_APP_CLIENT_ID", "").strip()
    secret = os.getenv("COGNITO_APP_CLIENT_SECRET", "").strip()
    if not domain or not client_id or not secret:
        return None
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    return {
        "token_url": f"{domain}/oauth2/token",
        "authorize_url": f"{domain}/oauth2/authorize",
        "logout_url": f"{domain}/logout",
        "client_id": client_id,
        "client_secret": secret,
    }


def is_oauth_callback_configured() -> bool:
    return _cognito_oauth_endpoints() is not None


_OAUTH_HTTP_TIMEOUT_S = float(os.getenv("SSO_OAUTH_HTTP_TIMEOUT_S", "8"))


def exchange_code_for_id_token(code: str, redirect_uri: str,
                               *, provider: str = "cognito") -> str:
    """POST /oauth2/token to Cognito; return the verified id_token.

    Raises :class:`SsoTokenError` on any failure. The returned token is
    NOT yet verified — call :func:`verify_id_token` next. Kept separate
    so the caller can attribute errors precisely.
    """
    if provider != "cognito":
        raise SsoTokenError(f"oauth code exchange not supported for '{provider}'")
    if not code or not isinstance(code, str):
        raise SsoTokenError("empty authorization code")
    if not redirect_uri or not isinstance(redirect_uri, str):
        raise SsoTokenError("missing redirect_uri")
    cfg = _cognito_oauth_endpoints()
    if cfg is None:
        raise SsoTokenError("oauth code exchange not configured")
    requests = _import_requests()
    try:
        resp = requests.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": cfg["client_id"],
            },
            auth=(cfg["client_id"], cfg["client_secret"]),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=_OAUTH_HTTP_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cognito /oauth2/token network error: %s", exc)
        raise SsoTokenError("oauth token endpoint unreachable") from exc
    if resp.status_code != 200:
        # Avoid echoing raw IdP body to avoid info-leak; log it server-side.
        logger.info("cognito /oauth2/token rejected (%s): %s",
                    resp.status_code, resp.text[:300])
        raise SsoTokenError("authorization code rejected")
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise SsoTokenError("oauth token response not JSON") from exc
    id_token = body.get("id_token")
    if not id_token or not isinstance(id_token, str):
        raise SsoTokenError("oauth token response missing id_token")
    return id_token

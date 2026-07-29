"""
Cognito JWT verification. Pure crypto against the User Pool's public JWKS endpoint —
no AWS credentials needed here, unlike file_io.py's S3 calls. Never trust tenant_id or
role from a request body/header; the only source of truth is the `users` row looked up
by the verified token's `sub` claim (see dependencies.py::get_current_user).
"""
from __future__ import annotations

import os
import ssl

import certifi
import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_APP_CLIENT_ID = os.environ["COGNITO_APP_CLIENT_ID"]

ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"

# ssl_context uses certifi's CA bundle explicitly rather than relying on the OS's default
# trust store — macOS python.org framework builds in particular often can't find system
# roots out of the box, and this way it's correct regardless of the underlying host's
# cert configuration (dev laptop today, EC2 later).
_ssl_context = ssl.create_default_context(cafile=certifi.where())

# Cached client — fetches + caches the JWKS keyset, only re-fetches on an unrecognized kid.
_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True, ssl_context=_ssl_context)


def verify_token(token: str) -> dict:
    """
    Verify a Cognito JWT (ID or access token) and return its claims.
    Raises HTTPException(401) on any verification failure.

    Cognito access tokens and ID tokens differ in one relevant way: ID tokens carry a
    standard `aud` claim; access tokens instead carry `client_id` and have no `aud` at
    all. Both must be checked against COGNITO_APP_CLIENT_ID to reject tokens issued for
    a different app client, so `verify_aud` is disabled in favor of doing this check
    manually for whichever claim the token actually has.
    """
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {e}"
        )

    token_use = claims.get("token_use")
    client_claim = claims.get("aud") if token_use == "id" else claims.get("client_id")
    if token_use not in ("id", "access") or client_claim != COGNITO_APP_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token was not issued for this app client",
        )

    return claims

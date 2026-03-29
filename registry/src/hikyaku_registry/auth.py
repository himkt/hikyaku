import hashlib
from typing import ClassVar

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hikyaku_registry.config import settings


def _extract_bearer_token(request: Request) -> str:
    """Extract and validate Bearer token from Authorization header.

    Returns the raw API key token.
    Raises HTTPException(401) if header is missing or malformed.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401)

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
        raise HTTPException(status_code=401)

    return parts[1].strip()


async def get_authenticated_agent(
    request: Request = None, store=None  # ty: ignore[invalid-parameter-default]
) -> tuple[str, str]:
    """Authenticate a request using Authorization + X-Agent-Id headers.

    Returns (agent_id, tenant_id) where tenant_id = SHA256(api_key).
    Raises HTTPException(401) if authentication fails.
    """
    if request is None or store is None:
        raise HTTPException(status_code=401)

    token = _extract_bearer_token(request)
    tenant_id = hashlib.sha256(token.encode()).hexdigest()

    agent_id = request.headers.get("x-agent-id")
    if not agent_id:
        raise HTTPException(status_code=401)

    agent_key_hash = await store._redis.hget(f"agent:{agent_id}", "api_key_hash")
    if agent_key_hash is None:
        raise HTTPException(status_code=401)

    if agent_key_hash != tenant_id:
        raise HTTPException(status_code=401)

    return (agent_id, tenant_id)


async def get_registration_tenant(
    request: Request = None, store=None  # ty: ignore[invalid-parameter-default]
) -> tuple[str, str] | None:
    """Extract optional Authorization header for registration flow.

    Returns None if no Authorization header (new tenant flow).
    Returns (api_key, api_key_hash) if valid auth with existing tenant.
    Raises HTTPException(401) if auth is malformed or tenant is dead.
    """
    if request is None or store is None:
        raise HTTPException(status_code=401)

    auth_header = request.headers.get("authorization")
    if not auth_header:
        return None

    token = _extract_bearer_token(request)
    api_key_hash = hashlib.sha256(token.encode()).hexdigest()

    tenant_count = await store._redis.scard(f"tenant:{api_key_hash}:agents")
    if tenant_count == 0:
        raise HTTPException(status_code=401)

    return (token, api_key_hash)


class Auth0Verifier:
    _jwks_client: ClassVar[jwt.PyJWKClient | None] = None

    @classmethod
    def get_jwks_client(cls) -> jwt.PyJWKClient:
        if cls._jwks_client is None:
            jwks_url = f"https://{settings.auth0_domain}/.well-known/jwks.json"
            cls._jwks_client = jwt.PyJWKClient(
                jwks_url, cache_keys=True, lifespan=60 * 60 * 24
            )
        return cls._jwks_client


async def verify_auth0_user(
    request: Request,
    cred: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> None:
    """Validate Auth0 JWT, store decoded token in request.scope."""
    try:
        signing_key = Auth0Verifier.get_jwks_client().get_signing_key_from_jwt(
            cred.credentials
        )
        decoded_token = jwt.decode(
            jwt=cred.credentials,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_client_id,
        )
    except jwt.exceptions.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.scope["token"] = cred.credentials
    request.scope["auth0"] = decoded_token


def get_user_id(request: Request) -> str:
    """Extract Auth0 sub claim from request scope (set by verify_auth0_user)."""
    if (user_id := request.scope.get("auth0", {}).get("sub")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user_id

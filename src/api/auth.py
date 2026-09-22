from __future__ import annotations

from fastapi import Header, HTTPException, status

from src.common.settings import API_KEYS


async def require_api_key(x_api_key: str | None = Header(default=None, alias='X-API-Key')) -> str:
    """FastAPI dependency enforcing a valid API key on protected routes.

    Clients must send: X-API-Key: <key>

    If API_KEYS is empty (nothing configured in the environment), every
    request is rejected rather than silently allowed through -- an empty
    allow-list should mean "nothing is authorized yet", not "auth is off".
    Use a dedicated internal-only route/network if you genuinely want no
    auth for local development.
    """
    if not API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='API_KEYS is not configured on the server.',
        )
    if x_api_key is None or x_api_key not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Missing or invalid API key.',
        )
    return x_api_key

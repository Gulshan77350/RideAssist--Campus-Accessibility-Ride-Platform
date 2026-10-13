import hmac
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from rideassist_common.config import get_settings
from rideassist_common.models import Role
from rideassist_common.ratelimit import hit
from rideassist_common.redis import get_redis
from rideassist_common.security import Principal, TokenError, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def get_principal(token: str = Depends(oauth2_scheme)) -> Principal:
    try:
        principal, _ = decode_token(token, "access")
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return principal


def require_roles(*roles: Role) -> Callable:
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        if principal.role != Role.SUPER_ADMIN and principal.college_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tenant bound to this account")
        return principal

    return _dep


async def require_internal_api_key(x_api_key: str = Header(default="")) -> None:
    """Service-to-service auth (same X-API-Key pattern as the reference monitoring project)."""
    if not hmac.compare_digest(x_api_key, get_settings().internal_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API key")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def rate_limit(scope: str, limit: int, window_seconds: int) -> Callable:
    """Per-IP fixed-window limiter backed by Redis, usable as a route dependency."""

    async def _dep(request: Request, redis=Depends(get_redis)) -> None:
        effective = max(1, int(limit * get_settings().rate_limit_multiplier))
        allowed, retry_after = await hit(
            redis, f"rl:{scope}:{client_ip(request)}", effective, window_seconds
        )
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests, slow down",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep

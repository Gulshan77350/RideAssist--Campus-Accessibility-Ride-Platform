import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from rideassist_common.config import get_settings
from rideassist_common.models import Role

# Used to keep login timing identical whether or not the email exists.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password_hash or _DUMMY_HASH
    ok = bcrypt.checkpw(password.encode(), candidate.encode())
    return ok and password_hash is not None


def generate_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(user_id: uuid.UUID, code: str) -> str:
    # Peppered HMAC bound to the user, so a leaked table row can't be replayed for another account.
    key = get_settings().otp_secret.encode()
    return hmac.new(key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def otp_matches(user_id: uuid.UUID, code: str, otp_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(user_id, code), otp_hash)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: Role
    college_id: uuid.UUID | None


def _encode(principal: Principal, token_type: str, ttl: timedelta) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(principal.user_id),
        "role": principal.role.value,
        "cid": str(principal.college_id) if principal.college_id else None,
        "typ": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_token_pair(principal: Principal) -> dict:
    s = get_settings()
    return {
        "access_token": _encode(principal, "access", timedelta(minutes=s.access_token_ttl_minutes)),
        "refresh_token": _encode(principal, "refresh", timedelta(days=s.refresh_token_ttl_days)),
        "token_type": "bearer",
        "expires_in": s.access_token_ttl_minutes * 60,
    }


class TokenError(Exception):
    pass


def decode_token(token: str, expected_type: str = "access") -> tuple[Principal, dict]:
    s = get_settings()
    try:
        claims = jwt.decode(
            token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != expected_type:
        raise TokenError("wrong token type")
    try:
        principal = Principal(
            user_id=uuid.UUID(claims["sub"]),
            role=Role(claims["role"]),
            college_id=uuid.UUID(claims["cid"]) if claims.get("cid") else None,
        )
    except (KeyError, ValueError) as exc:
        raise TokenError("malformed claims") from exc
    return principal, claims

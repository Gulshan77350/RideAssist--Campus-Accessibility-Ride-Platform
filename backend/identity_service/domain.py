"""Verification pipeline logic, kept out of the routers so it can be unit tested and reused
(e.g. the roster upload re-runs ``roster_match`` for students waiting on a document)."""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rideassist_common.config import get_settings
from rideassist_common.models import (
    College,
    EmailOTP,
    RosterEntry,
    StudentProfile,
    User,
    VerificationStatus,
)
from rideassist_common.notify import NotificationClient, NotificationError
from rideassist_common.policies import is_verified_pwd  # noqa: F401  (re-exported)
from rideassist_common.ratelimit import hit
from rideassist_common.security import generate_otp, hash_otp

NAME_MATCH_THRESHOLD = 0.85


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z ]", " ", ascii_name.lower()).split()
    return " ".join(sorted(tokens))  # order-insensitive: "Kumar Gulshan" == "Gulshan Kumar"


def names_match(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= NAME_MATCH_THRESHOLD


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1].lower()


async def roster_match(
    session: AsyncSession, college_id: uuid.UUID, enrollment_no: str, full_name: str, email: str
) -> bool:
    entry = await session.scalar(
        select(RosterEntry).where(
            RosterEntry.college_id == college_id, RosterEntry.enrollment_no == enrollment_no
        )
    )
    if entry is None or not entry.is_pwd:
        return False
    if entry.email and entry.email.lower() != email.lower():
        return False
    return names_match(entry.full_name, full_name)


def mark_verified(profile: StudentProfile, college: College, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    profile.verification_status = VerificationStatus.VERIFIED
    profile.verified_at = now
    profile.verification_expires_at = now + timedelta(days=college.reverification_days)
    profile.review_note = None


NEXT_STEPS = {
    VerificationStatus.PENDING_OTP: "Enter the 6-digit code sent to your college email.",
    VerificationStatus.AWAITING_DOCUMENT: (
        "Your enrollment number isn't in the college's PwD roster yet. Upload your UDID card or "
        "a college-issued disability certificate for review."
    ),
    VerificationStatus.PENDING_REVIEW: "Your document is with the disability cell for review.",
    VerificationStatus.VERIFIED: "You're verified and can book rides.",
    VerificationStatus.REJECTED: (
        "Verification was rejected. See the note and upload a new document."
    ),
}


async def issue_otp(
    session: AsyncSession, redis: Redis, notifier: NotificationClient, user: User
) -> None:
    """Throttled OTP issue: per-user cooldown + hourly cap, previous codes invalidated."""
    s = get_settings()
    cooldown_key = f"otp:cooldown:{user.id}"
    if not await redis.set(cooldown_key, "1", ex=s.otp_resend_cooldown_seconds, nx=True):
        retry = await redis.ttl(cooldown_key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Please wait {retry}s before requesting another code",
            headers={"Retry-After": str(max(retry, 1))},
        )
    allowed, retry = await hit(redis, f"otp:sends:{user.id}", s.otp_max_sends_per_hour, 3600)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many codes requested, try again later",
            headers={"Retry-After": str(retry)},
        )

    now = datetime.now(UTC)
    await session.execute(
        update(EmailOTP)
        .where(EmailOTP.user_id == user.id, EmailOTP.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_otp()
    session.add(
        EmailOTP(
            user_id=user.id,
            otp_hash=hash_otp(user.id, code),
            expires_at=now + timedelta(seconds=s.otp_ttl_seconds),
        )
    )
    await session.commit()

    minutes = s.otp_ttl_seconds // 60
    try:
        await notifier.send_email(
            user.email,
            "Your RideAssist verification code",
            f"Hi {user.full_name},\n\nYour RideAssist verification code is {code}.\n"
            f"It expires in {minutes} minutes. If you didn't request it, ignore this email.\n",
        )
    except NotificationError as exc:
        await redis.delete(cooldown_key)  # don't make the user wait for a code they never got
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't send the code, please retry shortly"
        ) from exc

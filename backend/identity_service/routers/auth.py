from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from identity_service import domain
from identity_service.schemas import (
    AccessibilityIn,
    CampusOut,
    MeOut,
    OtpConfirmIn,
    OtpConfirmOut,
    OtpSendIn,
    RefreshIn,
    RegisterOut,
    StudentInfo,
    StudentRegisterIn,
    TokenOut,
)
from rideassist_common.audit import record_audit
from rideassist_common.config import get_settings
from rideassist_common.db import get_session
from rideassist_common.deps import client_ip, get_principal, rate_limit, require_roles
from rideassist_common.geo import latlng
from rideassist_common.models import (
    College,
    EmailOTP,
    Role,
    StudentProfile,
    User,
    VerificationStatus,
)
from rideassist_common.notify import NotificationClient, get_notifier
from rideassist_common.redis import get_redis
from rideassist_common.security import (
    Principal,
    TokenError,
    create_token_pair,
    decode_token,
    hash_password,
    otp_matches,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["auth & verification"])

INVALID_CODE = "Invalid or expired code"


@router.post(
    "/auth/register/student",
    response_model=RegisterOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", 10, 900))],
)
async def register_student(
    body: StudentRegisterIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> RegisterOut:
    """Step 1 of verification: college email + enrollment number.

    The roster lookup result is stored but deliberately *not* revealed until the student has
    proven they own the inbox, so this endpoint can't be used to probe who is on the PwD roster.
    """
    email = body.email.lower()
    college = await session.scalar(
        select(College).where(
            College.email_domain == domain.email_domain(email), College.is_active.is_(True)
        )
    )
    if college is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Use your official college email. Your college may not be onboarded yet.",
        )
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This email is already registered. Log in, or request a new code if you didn't verify.",
        )

    existing = await session.scalar(
        select(StudentProfile).where(
            StudentProfile.college_id == college.id,
            StudentProfile.enrollment_no == body.enrollment_no,
        )
    )
    if existing is not None:
        squat_window = datetime.now(UTC) - timedelta(seconds=get_settings().otp_ttl_seconds)
        if (
            existing.verification_status == VerificationStatus.PENDING_OTP
            and existing.created_at < squat_window
        ):
            # Abandoned, never-verified claim on this roll number: free it instead of letting
            # anyone permanently squat a classmate's enrollment number.
            await session.execute(delete(User).where(User.id == existing.user_id))
        else:
            record_audit(
                session,
                "anomaly.duplicate_enrollment",
                college_id=college.id,
                target_type="enrollment",
                target_id=body.enrollment_no,
                ip=client_ip(request),
                attempted_email=email,
            )
            await session.commit()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This enrollment number is already registered. Contact your disability cell "
                "if this wasn't you.",
            )

    matched = await domain.roster_match(
        session, college.id, body.enrollment_no, body.full_name, email
    )
    user = User(
        college_id=college.id,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        phone=body.phone,
        role=Role.RIDER,
    )
    session.add(user)
    await session.flush()
    session.add(
        StudentProfile(
            user_id=user.id,
            college_id=college.id,
            enrollment_no=body.enrollment_no,
            verification_status=VerificationStatus.PENDING_OTP,
            roster_matched=matched,
        )
    )
    record_audit(
        session,
        "student.registered",
        college_id=college.id,
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=client_ip(request),
    )
    await session.commit()

    await domain.issue_otp(session, redis, notifier, user)
    return RegisterOut(
        user_id=user.id,
        verification_status=VerificationStatus.PENDING_OTP,
        message=f"We sent a 6-digit code to {email}.",
    )


@router.post(
    "/verification/otp/send",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("otp-send", 10, 900))],
)
async def send_otp(
    body: OtpSendIn,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> dict:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    profile = await session.get(StudentProfile, user.id) if user else None
    if profile is not None and profile.verification_status == VerificationStatus.PENDING_OTP:
        await domain.issue_otp(session, redis, notifier, user)
    # Same response either way: no account enumeration through this endpoint.
    return {"message": "If that account is awaiting verification, a new code is on its way."}


@router.post(
    "/verification/otp/confirm",
    response_model=OtpConfirmOut,
    dependencies=[Depends(rate_limit("otp-confirm", 30, 900))],
)
async def confirm_otp(
    body: OtpConfirmIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> OtpConfirmOut:
    s = get_settings()
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    profile = await session.get(StudentProfile, user.id) if user else None
    if profile is None or profile.verification_status != VerificationStatus.PENDING_OTP:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_CODE)

    now = datetime.now(UTC)
    otp = await session.scalar(
        select(EmailOTP)
        .where(EmailOTP.user_id == user.id, EmailOTP.consumed_at.is_(None))
        .order_by(EmailOTP.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if otp is None or otp.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_CODE)
    if otp.attempts >= s.otp_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong attempts. Request a new code."
        )

    otp.attempts += 1
    if not otp_matches(user.id, body.code, otp.otp_hash):
        if otp.attempts >= s.otp_max_attempts:
            record_audit(
                session,
                "anomaly.otp_locked",
                college_id=user.college_id,
                target_type="user",
                target_id=user.id,
                ip=client_ip(request),
            )
        await session.commit()
        remaining = s.otp_max_attempts - otp.attempts
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{INVALID_CODE}. {remaining} attempt(s) left."
        )

    otp.consumed_at = now
    profile.email_verified_at = now
    college = await session.get(College, user.college_id)
    if profile.roster_matched:
        domain.mark_verified(profile, college, now)
        record_audit(
            session,
            "verification.auto_approved",
            college_id=college.id,
            target_type="user",
            target_id=user.id,
            reason="roster_match",
        )
    else:
        profile.verification_status = VerificationStatus.AWAITING_DOCUMENT
    await session.commit()

    tokens = create_token_pair(Principal(user.id, user.role, user.college_id))
    return OtpConfirmOut(
        **tokens,
        verification_status=profile.verification_status,
        message=domain.NEXT_STEPS[profile.verification_status],
    )


@router.post(
    "/auth/token",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit("login", 20, 300))],
)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    """OAuth2 password flow (username = email). Works from Swagger's Authorize button."""
    user = await session.scalar(select(User).where(User.email == form.username.lower()))
    if not verify_password(form.password, user.password_hash if user else None) or not (
        user and user.is_active
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if user.role == Role.RIDER:
        profile = await session.get(StudentProfile, user.id)
        if profile is None or profile.email_verified_at is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Verify your college email with the code we sent first"
            )
    return TokenOut(**create_token_pair(Principal(user.id, user.role, user.college_id)))


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(
    body: RefreshIn,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> TokenOut:
    """Rotating refresh tokens: each one is single-use, a replay is rejected."""
    try:
        principal, claims = decode_token(body.refresh_token, "refresh")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from exc
    ttl = max(int(claims["exp"] - datetime.now(UTC).timestamp()), 1)
    if not await redis.set(f"refresh:used:{claims['jti']}", "1", ex=ttl, nx=True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token already used")
    user = await session.get(User, principal.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    return TokenOut(**create_token_pair(Principal(user.id, user.role, user.college_id)))


def _campus(college: College) -> CampusOut:
    lat, lng = latlng(college.campus_center)
    return CampusOut(lat=lat, lng=lng, radius_m=college.campus_radius_m)


async def _me(session: AsyncSession, principal: Principal) -> MeOut:
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    college = await session.get(College, user.college_id) if user.college_id else None
    profile = await session.get(StudentProfile, user.id) if user.role == Role.RIDER else None
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        role=user.role,
        college_id=user.college_id,
        college_name=college.name if college else None,
        campus=_campus(college) if college else None,
        student=StudentInfo.model_validate(profile) if profile else None,
    )


@router.get("/auth/me", response_model=MeOut)
async def me(
    principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)
) -> MeOut:
    return await _me(session, principal)


@router.patch("/auth/me/accessibility", response_model=MeOut)
async def update_accessibility(
    body: AccessibilityIn,
    principal: Principal = Depends(require_roles(Role.RIDER)),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    profile = await session.get(StudentProfile, principal.user_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await session.commit()
    return await _me(session, principal)

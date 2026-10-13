from datetime import UTC, datetime

from rideassist_common.models import StudentProfile, VerificationStatus


def is_verified_pwd(profile: StudentProfile | None, now: datetime | None = None) -> bool:
    """The booking gate: verified AND not past the college's re-verification date."""
    if profile is None or profile.verification_status != VerificationStatus.VERIFIED:
        return False
    expires = profile.verification_expires_at
    return expires is None or expires > (now or datetime.now(UTC))

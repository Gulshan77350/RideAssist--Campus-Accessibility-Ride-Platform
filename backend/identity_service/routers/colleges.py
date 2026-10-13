from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from identity_service.schemas import CollegeCreateIn, CollegeLookupOut, CollegeOut
from rideassist_common.audit import record_audit
from rideassist_common.db import get_session
from rideassist_common.deps import client_ip, require_roles
from rideassist_common.geo import latlng, point
from rideassist_common.models import College, Role, User
from rideassist_common.security import Principal, hash_password

router = APIRouter(prefix="/api/v1/colleges", tags=["tenants"])


def _out(c: College) -> CollegeOut:
    lat, lng = latlng(c.campus_center)
    return CollegeOut(
        id=c.id,
        name=c.name,
        email_domain=c.email_domain,
        subdomain=c.subdomain,
        campus_lat=lat,
        campus_lng=lng,
        campus_radius_m=c.campus_radius_m,
        is_active=c.is_active,
    )


@router.get("/lookup", response_model=CollegeLookupOut)
async def lookup_by_email_domain(
    domain: str = Query(..., min_length=3, max_length=120),
    session: AsyncSession = Depends(get_session),
) -> CollegeLookupOut:
    """Public: lets the sign-up screen show 'IIIT Allahabad' as soon as the email is typed."""
    college = await session.scalar(
        select(College).where(
            College.email_domain == domain.lower().lstrip("@"), College.is_active.is_(True)
        )
    )
    if college is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "College not onboarded")
    return CollegeLookupOut(name=college.name, subdomain=college.subdomain)


@router.get("", response_model=list[CollegeOut])
async def list_colleges(
    _: Principal = Depends(require_roles(Role.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> list[CollegeOut]:
    return [_out(c) for c in await session.scalars(select(College).order_by(College.name))]


@router.post("", response_model=CollegeOut, status_code=status.HTTP_201_CREATED)
async def onboard_college(
    body: CollegeCreateIn,
    request: Request,
    principal: Principal = Depends(require_roles(Role.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> CollegeOut:
    """Super-admin onboarding: creates the tenant and its first college-admin account."""
    clash = await session.scalar(
        select(College.id).where(
            or_(College.email_domain == body.email_domain, College.subdomain == body.subdomain)
        )
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Domain or subdomain already onboarded")
    if await session.scalar(select(User.id).where(User.email == body.admin_email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Admin email already in use")

    college = College(
        name=body.name,
        email_domain=body.email_domain,
        subdomain=body.subdomain,
        campus_center=point(body.campus_lat, body.campus_lng),
        campus_radius_m=body.campus_radius_m,
        reverification_days=body.reverification_days,
        security_email=body.security_email,
    )
    session.add(college)
    await session.flush()
    session.add(
        User(
            college_id=college.id,
            email=body.admin_email.lower(),
            password_hash=hash_password(body.admin_password),
            full_name=body.admin_full_name,
            role=Role.COLLEGE_ADMIN,
        )
    )
    record_audit(
        session,
        "tenant.onboarded",
        college_id=college.id,
        actor_id=principal.user_id,
        target_type="college",
        target_id=college.id,
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(college)
    return _out(college)

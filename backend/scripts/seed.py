"""Idempotent dev seed: one college (IIIT Allahabad), its admin, roster, fleet and drivers.

python -m scripts.seed
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from rideassist_common.db import get_engine, get_sessionmaker
from rideassist_common.geo import point
from rideassist_common.models import (
    College,
    DriverProfile,
    Role,
    RosterEntry,
    StudentProfile,
    User,
    Vehicle,
    VehicleKind,
    VerificationStatus,
)
from rideassist_common.security import hash_password

CAMPUS = (25.4304, 81.7713)  # IIIT Allahabad, Jhalwa
SUPER_ADMIN = ("superadmin@rideassist.dev", "SuperAdmin@123")
COLLEGE_ADMIN = ("admin@iiita.ac.in", "Admin@12345")
DRIVER_PASSWORD = "Driver@12345"
# Pre-verified rider for the login page's "Student" demo button.
DEMO_STUDENT = ("iit2023004@iiita.ac.in", "IIT2023004", "Sneha Iyer", "Student@123")
ROSTER = [
    ("IIT2023001", "Aarav Sharma", "iit2023001@iiita.ac.in"),
    ("IIT2023002", "Priya Verma", None),
    ("IIT2023003", "Rohan Gupta", None),
    ("IIT2023004", "Sneha Iyer", None),
]
FLEET = [
    (
        "UP70EV0001",
        VehicleKind.WHEELCHAIR_ACCESSIBLE,
        4,
        "ramesh.driver@iiita.ac.in",
        "Ramesh Yadav",
        "+919800000001",
    ),
    (
        "UP70EV0002",
        VehicleKind.STANDARD,
        6,
        "suresh.driver@iiita.ac.in",
        "Suresh Kumar",
        "+919800000002",
    ),
]


async def seed() -> None:
    async with get_sessionmaker()() as session:
        existing = await session.scalar(
            select(College).where(College.email_domain == "iiita.ac.in")
        )
        if existing:
            print("seed: tenant already present")
            await ensure_demo_student(session, existing)
            return

        session.add(
            User(
                email=SUPER_ADMIN[0],
                password_hash=hash_password(SUPER_ADMIN[1]),
                full_name="Platform Admin",
                role=Role.SUPER_ADMIN,
            )
        )
        college = College(
            name="IIIT Allahabad",
            email_domain="iiita.ac.in",
            subdomain="iiita",
            campus_center=point(*CAMPUS),
            campus_radius_m=2000,
            reverification_days=365,
            security_email="security@iiita.ac.in",
        )
        session.add(college)
        await session.flush()

        session.add(
            User(
                college_id=college.id,
                email=COLLEGE_ADMIN[0],
                password_hash=hash_password(COLLEGE_ADMIN[1]),
                full_name="Disability Cell Officer",
                role=Role.COLLEGE_ADMIN,
            )
        )
        session.add_all(
            RosterEntry(college_id=college.id, enrollment_no=e, full_name=n, email=m, is_pwd=True)
            for e, n, m in ROSTER
        )
        for plate, kind, capacity, email, name, phone in FLEET:
            vehicle = Vehicle(college_id=college.id, plate_no=plate, kind=kind, capacity=capacity)
            driver = User(
                college_id=college.id,
                email=email,
                password_hash=hash_password(DRIVER_PASSWORD),
                full_name=name,
                phone=phone,
                role=Role.DRIVER,
            )
            session.add_all([vehicle, driver])
            await session.flush()
            session.add(
                DriverProfile(user_id=driver.id, college_id=college.id, vehicle_id=vehicle.id)
            )

        await session.commit()
        print("seed: created IIIT Allahabad tenant, admin, roster, 2 vehicles, 2 drivers")
        await ensure_demo_student(session, college)
    await get_engine().dispose()


async def ensure_demo_student(session, college: College) -> None:
    email, enrollment, name, password = DEMO_STUDENT
    if await session.scalar(select(User.id).where(User.email == email)):
        return
    now = datetime.now(UTC)
    user = User(
        college_id=college.id,
        email=email,
        password_hash=hash_password(password),
        full_name=name,
        phone="+919800000100",
        role=Role.RIDER,
    )
    session.add(user)
    await session.flush()
    session.add(
        StudentProfile(
            user_id=user.id,
            college_id=college.id,
            enrollment_no=enrollment,
            verification_status=VerificationStatus.VERIFIED,
            roster_matched=True,
            email_verified_at=now,
            verified_at=now,
            verification_expires_at=now + timedelta(days=college.reverification_days),
            needs_wheelchair=True,
            pickup_notes="Uses a manual wheelchair; ramp needed at the hostel side gate",
            emergency_contact_phone="+919800000200",
        )
    )
    await session.commit()
    print(f"seed: created verified demo student {email}")


if __name__ == "__main__":
    asyncio.run(seed())

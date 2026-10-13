"""Integration-test harness: real PostGIS + Redis (from docker compose), fake email/push/storage.

Run with:  docker compose --profile test run --rm tests
"""

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from identity_service.main import app as identity_app
from ride_service.main import app as rides_app
from rideassist_common.config import DEV_DOCUMENT_ENCRYPTION_KEY
from rideassist_common.db import Base, get_engine, get_sessionmaker
from rideassist_common.geo import point
from rideassist_common.models import (
    College,
    DriverProfile,
    Role,
    RosterEntry,
    User,
    Vehicle,
    VehicleKind,
)
from rideassist_common.notify import get_notifier
from rideassist_common.redis import get_redis
from rideassist_common.security import hash_password
from rideassist_common.storage import DocumentVault, get_vault

CAMPUS = (25.4304, 81.7713)
PASSWORD = "Passw0rd!23"


@dataclass
class FakeNotifier:
    emails: list[dict] = field(default_factory=list)
    pushes: list[dict] = field(default_factory=list)

    async def send_email(self, to: str, subject: str, body: str) -> None:
        self.emails.append({"to": to, "subject": subject, "body": body})

    async def push(self, user_id, title: str, body: str, data: dict | None = None) -> None:
        self.pushes.append({"user_id": str(user_id), "title": title, "body": body, "data": data})

    def last_otp(self, to: str) -> str:
        for mail in reversed(self.emails):
            if mail["to"] == to and (m := re.search(r"\b(\d{6})\b", mail["body"])):
                return m.group(1)
        raise AssertionError(f"no OTP email for {to}")


class MemoryStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    async def get(self, key: str) -> bytes:
        return self.blobs[key]


@pytest.fixture(scope="session", autouse=True)
async def schema():
    """Fresh schema built by the real Alembic migration (so the migration itself is tested)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "alembic", "upgrade", "head")
    assert await proc.wait() == 0, "alembic upgrade failed"
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_state(schema):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    redis = await get_redis()
    await redis.flushdb()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
async def identity(notifier, store):
    vault = DocumentVault(store, DEV_DOCUMENT_ENCRYPTION_KEY)
    identity_app.dependency_overrides = {get_notifier: lambda: notifier, get_vault: lambda: vault}
    async with AsyncClient(transport=ASGITransport(app=identity_app), base_url="http://test") as c:
        yield c
    identity_app.dependency_overrides = {}


@pytest.fixture
async def rides(notifier):
    rides_app.dependency_overrides = {get_notifier: lambda: notifier}
    async with AsyncClient(transport=ASGITransport(app=rides_app), base_url="http://test") as c:
        yield c
    rides_app.dependency_overrides = {}


@dataclass
class Tenant:
    college_id: uuid.UUID
    domain: str
    admin_email: str
    drivers: dict[str, str]  # vehicle kind -> driver email


async def make_tenant(domain: str = "iiita.ac.in", center=CAMPUS) -> Tenant:
    async with get_sessionmaker()() as s:
        college = College(
            name=f"College {domain}",
            email_domain=domain,
            subdomain=domain.split(".")[0],
            campus_center=point(*center),
            campus_radius_m=2000,
            reverification_days=365,
            security_email=f"security@{domain}",
        )
        s.add(college)
        await s.flush()
        admin_email = f"admin@{domain}"
        s.add(
            User(
                college_id=college.id,
                email=admin_email,
                password_hash=hash_password(PASSWORD),
                full_name="Cell Officer",
                role=Role.COLLEGE_ADMIN,
            )
        )
        s.add(
            RosterEntry(
                college_id=college.id,
                enrollment_no="IIT2023001",
                full_name="Aarav Sharma",
                is_pwd=True,
            )
        )
        drivers = {}
        for i, kind in enumerate(VehicleKind):
            v = Vehicle(college_id=college.id, plate_no=f"UP70EV{i:04d}", kind=kind, capacity=4)
            email = f"driver{i}@{domain}"
            u = User(
                college_id=college.id,
                email=email,
                password_hash=hash_password(PASSWORD),
                full_name=f"Driver {i}",
                phone=f"+91980000000{i}",
                role=Role.DRIVER,
            )
            s.add_all([v, u])
            await s.flush()
            s.add(DriverProfile(user_id=u.id, college_id=college.id, vehicle_id=v.id))
            drivers[kind.value] = email
        await s.commit()
        return Tenant(college.id, domain, admin_email, drivers)


@pytest.fixture
async def tenant() -> Tenant:
    return await make_tenant()


async def auth_headers(client: AsyncClient, email: str, password: str = PASSWORD) -> dict:
    resp = await client.post("/api/v1/auth/token", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def register_and_confirm(
    client: AsyncClient,
    notifier: FakeNotifier,
    email: str,
    enrollment_no: str,
    full_name: str,
) -> dict:
    resp = await client.post(
        "/api/v1/auth/register/student",
        json={
            "email": email,
            "enrollment_no": enrollment_no,
            "full_name": full_name,
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/verification/otp/confirm", json={"email": email, "code": notifier.last_otp(email)}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def verified_rider(identity, notifier, tenant) -> dict:
    body = await register_and_confirm(
        identity, notifier, "iit2023001@iiita.ac.in", "IIT2023001", "Aarav Sharma"
    )
    assert body["verification_status"] == "verified"
    return {"Authorization": f"Bearer {body['access_token']}"}

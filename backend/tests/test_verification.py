from sqlalchemy import update

from rideassist_common.db import get_sessionmaker
from rideassist_common.models import EmailOTP
from tests.conftest import PASSWORD, auth_headers, make_tenant, register_and_confirm

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def test_roster_match_is_auto_verified_after_otp(identity, notifier, tenant):
    resp = await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "IIT2023001@iiita.ac.in",
            "enrollment_no": "iit2023001",
            "full_name": "sharma aarav",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201
    # The roster result is NOT revealed before the inbox is proven.
    assert resp.json()["verification_status"] == "pending_otp"

    code = notifier.last_otp("iit2023001@iiita.ac.in")
    resp = await identity.post(
        "/api/v1/verification/otp/confirm", json={"email": "iit2023001@iiita.ac.in", "code": code}
    )
    assert resp.status_code == 200
    assert resp.json()["verification_status"] == "verified"

    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    status = (await identity.get("/api/v1/verification/status", headers=headers)).json()
    assert status["can_book_rides"] is True
    assert status["verification_expires_at"] is not None


async def test_unknown_domain_rejected(identity, tenant):
    resp = await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "someone@gmail.com",
            "enrollment_no": "X123",
            "full_name": "Some One",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 422


async def test_document_review_flow(identity, notifier, tenant):
    body = await register_and_confirm(
        identity, notifier, "neha@iiita.ac.in", "IIT2023999", "Neha Singh"
    )
    assert body["verification_status"] == "awaiting_document"
    student = {"Authorization": f"Bearer {body['access_token']}"}

    # Content-type is sniffed from bytes; a disguised text file is refused.
    bad = await identity.post(
        "/api/v1/verification/documents",
        headers=student,
        data={"doc_type": "udid"},
        files={"file": ("udid.png", b"not really a png", "image/png")},
    )
    assert bad.status_code == 415

    up = await identity.post(
        "/api/v1/verification/documents",
        headers=student,
        data={"doc_type": "udid"},
        files={"file": ("udid.png", PNG, "image/png")},
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]

    admin = await auth_headers(identity, tenant.admin_email)
    queue = (await identity.get("/api/v1/admin/verifications", headers=admin)).json()
    assert [q["email"] for q in queue] == ["neha@iiita.ac.in"]
    student_id = queue[0]["student_id"]

    file = await identity.get(f"/api/v1/admin/verifications/documents/{doc_id}/file", headers=admin)
    assert file.status_code == 200 and file.content == PNG  # decrypted for the reviewer only

    approved = await identity.post(
        f"/api/v1/admin/verifications/{student_id}/approve", headers=admin, json={}
    )
    assert approved.status_code == 200
    assert approved.json()["verification_status"] == "verified"
    assert any(m["subject"] == "You're verified on RideAssist" for m in notifier.emails)


async def test_reject_requires_note_and_allows_reupload(identity, notifier, tenant):
    body = await register_and_confirm(identity, notifier, "x@iiita.ac.in", "IIT9", "Xavier Lal")
    student = {"Authorization": f"Bearer {body['access_token']}"}
    await identity.post(
        "/api/v1/verification/documents",
        headers=student,
        data={"doc_type": "udid"},
        files={"file": ("a.png", PNG, "image/png")},
    )
    admin = await auth_headers(identity, tenant.admin_email)
    sid = (await identity.get("/api/v1/admin/verifications", headers=admin)).json()[0]["student_id"]
    assert (
        await identity.post(f"/api/v1/admin/verifications/{sid}/reject", headers=admin, json={})
    ).status_code == 422
    rej = await identity.post(
        f"/api/v1/admin/verifications/{sid}/reject", headers=admin, json={"note": "Blurry scan"}
    )
    assert rej.json()["verification_status"] == "rejected"
    again = await identity.post(
        "/api/v1/verification/documents",
        headers=student,
        data={"doc_type": "udid"},
        files={"file": ("b.png", PNG, "image/png")},
    )
    assert again.status_code == 201


async def test_wrong_otp_locks_after_max_attempts(identity, notifier, tenant):
    await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "brute@iiita.ac.in",
            "enrollment_no": "IIT777",
            "full_name": "Bru Te",
            "password": PASSWORD,
        },
    )
    real = notifier.last_otp("brute@iiita.ac.in")
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = await identity.post(
            "/api/v1/verification/otp/confirm", json={"email": "brute@iiita.ac.in", "code": wrong}
        )
        assert r.status_code == 400
    # Even the correct code is refused once the OTP is locked.
    r = await identity.post(
        "/api/v1/verification/otp/confirm", json={"email": "brute@iiita.ac.in", "code": real}
    )
    assert r.status_code == 429

    admin = await auth_headers(identity, tenant.admin_email)
    anomalies = (await identity.get("/api/v1/admin/anomalies", headers=admin)).json()
    assert [a["action"] for a in anomalies] == ["anomaly.otp_locked"]


async def test_expired_otp_rejected(identity, notifier, tenant):
    await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "late@iiita.ac.in",
            "enrollment_no": "IIT888",
            "full_name": "Late Comer",
            "password": PASSWORD,
        },
    )
    async with get_sessionmaker()() as s:
        await s.execute(update(EmailOTP).values(expires_at=EmailOTP.created_at))
        await s.commit()
    r = await identity.post(
        "/api/v1/verification/otp/confirm",
        json={"email": "late@iiita.ac.in", "code": notifier.last_otp("late@iiita.ac.in")},
    )
    assert r.status_code == 400


async def test_otp_resend_cooldown(identity, notifier, tenant):
    await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "re@iiita.ac.in",
            "enrollment_no": "IIT555",
            "full_name": "Re Send",
            "password": PASSWORD,
        },
    )
    r = await identity.post("/api/v1/verification/otp/send", json={"email": "re@iiita.ac.in"})
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_login_blocked_until_email_verified(identity, notifier, tenant):
    await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "slow@iiita.ac.in",
            "enrollment_no": "IIT444",
            "full_name": "Slow Poke",
            "password": PASSWORD,
        },
    )
    r = await identity.post(
        "/api/v1/auth/token", data={"username": "slow@iiita.ac.in", "password": PASSWORD}
    )
    assert r.status_code == 403


async def test_duplicate_enrollment_flagged(identity, notifier, tenant):
    await register_and_confirm(
        identity, notifier, "iit2023001@iiita.ac.in", "IIT2023001", "Aarav Sharma"
    )
    r = await identity.post(
        "/api/v1/auth/register/student",
        json={
            "email": "imposter@iiita.ac.in",
            "enrollment_no": "IIT2023001",
            "full_name": "Aarav Sharma",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 409
    admin = await auth_headers(identity, tenant.admin_email)
    anomalies = (await identity.get("/api/v1/admin/anomalies", headers=admin)).json()
    assert anomalies[0]["action"] == "anomaly.duplicate_enrollment"


async def test_roster_upload_auto_verifies_waiting_students(identity, notifier, tenant):
    body = await register_and_confirm(
        identity, notifier, "kabir@iiita.ac.in", "IIT2024010", "Kabir Das"
    )
    assert body["verification_status"] == "awaiting_document"

    admin = await auth_headers(identity, tenant.admin_email)
    csv = "enrollment_no,full_name,email,is_pwd\nIIT2024010,Kabir Das,,yes\n,missing,,\n"
    r = await identity.post(
        "/api/v1/admin/roster", headers=admin, files={"file": ("roster.csv", csv, "text/csv")}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "rows_processed": 1,
        "rows_rejected": [r.json()["rows_rejected"][0]],
        "auto_verified_students": 1,
    }


async def test_tenant_isolation(identity, notifier, tenant):
    other = await make_tenant("mnnit.ac.in", (25.4920, 81.8639))
    body = await register_and_confirm(identity, notifier, "a@iiita.ac.in", "IIT321", "Anil Kapoor")
    student = {"Authorization": f"Bearer {body['access_token']}"}
    await identity.post(
        "/api/v1/verification/documents",
        headers=student,
        data={"doc_type": "udid"},
        files={"file": ("a.png", PNG, "image/png")},
    )
    me = (await identity.get("/api/v1/auth/me", headers=student)).json()

    foreign_admin = await auth_headers(identity, other.admin_email)
    assert (await identity.get("/api/v1/admin/verifications", headers=foreign_admin)).json() == []
    r = await identity.post(
        f"/api/v1/admin/verifications/{me['id']}/approve", headers=foreign_admin, json={}
    )
    assert r.status_code == 404  # not 403: other tenants can't even learn it exists


async def test_refresh_token_is_single_use(identity, verified_rider, notifier):
    r = await identity.post(
        "/api/v1/auth/token", data={"username": "iit2023001@iiita.ac.in", "password": PASSWORD}
    )
    refresh = r.json()["refresh_token"]
    first = await identity.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert first.status_code == 200
    replay = await identity.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401


async def test_role_guard(identity, tenant, verified_rider):
    r = await identity.get("/api/v1/admin/verifications", headers=verified_rider)
    assert r.status_code == 403

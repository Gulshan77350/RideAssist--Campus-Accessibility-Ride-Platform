"""College-admin endpoints: roster sync, verification review queue, staff accounts, anomalies.

Every query is filtered on the admin's own ``college_id``; a record from another tenant is
reported as 404, never 403, so tenants can't even learn that it exists.
"""

import csv
import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from identity_service import domain
from identity_service.schemas import (
    AnomalyOut,
    DocumentOut,
    DriverCreateIn,
    PendingVerificationOut,
    RejectIn,
    ReviewIn,
    RosterUploadOut,
    StaffOut,
)
from rideassist_common.audit import record_audit
from rideassist_common.db import get_session
from rideassist_common.deps import client_ip, require_roles
from rideassist_common.models import (
    AuditLog,
    College,
    DocumentStatus,
    DriverProfile,
    Role,
    RosterEntry,
    StudentProfile,
    User,
    VerificationDocument,
    VerificationStatus,
)
from rideassist_common.notify import NotificationClient, NotificationError, get_notifier
from rideassist_common.security import Principal, hash_password
from rideassist_common.storage import DocumentVault, get_vault

router = APIRouter(prefix="/api/v1/admin", tags=["college admin"])
admin_only = require_roles(Role.COLLEGE_ADMIN)

ROSTER_COLUMNS = {"enrollment_no", "full_name"}
TRUTHY = {"1", "true", "yes", "y"}


async def _student_in_tenant(
    session: AsyncSession, student_id: uuid.UUID, college_id: uuid.UUID
) -> tuple[StudentProfile, User]:
    row = (
        await session.execute(
            select(StudentProfile, User)
            .join(User, User.id == StudentProfile.user_id)
            .where(StudentProfile.user_id == student_id, StudentProfile.college_id == college_id)
            .with_for_update(of=StudentProfile)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return row[0], row[1]


async def _notify_quietly(notifier: NotificationClient, to: str, subject: str, body: str) -> None:
    try:
        await notifier.send_email(to, subject, body)
    except NotificationError:
        pass  # the decision is already committed; email is informational


@router.get("/verifications", response_model=list[PendingVerificationOut])
async def list_verifications(
    status_filter: VerificationStatus = Query(VerificationStatus.PENDING_REVIEW, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> list[PendingVerificationOut]:
    rows = (
        await session.execute(
            select(StudentProfile, User)
            .join(User, User.id == StudentProfile.user_id)
            .where(
                StudentProfile.college_id == principal.college_id,
                StudentProfile.verification_status == status_filter,
            )
            .order_by(StudentProfile.created_at)
            .limit(limit)
        )
    ).all()
    docs_by_student: dict[uuid.UUID, list[VerificationDocument]] = {}
    if rows:
        docs = await session.scalars(
            select(VerificationDocument)
            .where(VerificationDocument.student_id.in_([p.user_id for p, _ in rows]))
            .order_by(VerificationDocument.uploaded_at.desc())
        )
        for d in docs:
            docs_by_student.setdefault(d.student_id, []).append(d)
    return [
        PendingVerificationOut(
            student_id=p.user_id,
            full_name=u.full_name,
            email=u.email,
            enrollment_no=p.enrollment_no,
            verification_status=p.verification_status,
            roster_matched=p.roster_matched,
            registered_at=p.created_at,
            documents=[DocumentOut.model_validate(d) for d in docs_by_student.get(p.user_id, [])],
        )
        for p, u in rows
    ]


@router.get("/verifications/documents/{document_id}/file")
async def view_document(
    document_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
    vault: DocumentVault = Depends(get_vault),
) -> Response:
    """Decrypts on the fly for the reviewing admin; every view is written to the audit trail."""
    doc = await session.scalar(
        select(VerificationDocument).where(
            VerificationDocument.id == document_id,
            VerificationDocument.college_id == principal.college_id,
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    data = await vault.load(doc.object_key)
    record_audit(
        session,
        "document.viewed",
        college_id=principal.college_id,
        actor_id=principal.user_id,
        target_type="document",
        target_id=doc.id,
        ip=client_ip(request),
    )
    await session.commit()
    return Response(
        data,
        media_type=doc.content_type,
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
    )


@router.post("/verifications/{student_id}/approve", response_model=PendingVerificationOut)
async def approve(
    student_id: uuid.UUID,
    body: ReviewIn,
    request: Request,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
    notifier: NotificationClient = Depends(get_notifier),
) -> PendingVerificationOut:
    return await _review(
        session, notifier, request, principal, student_id, approve=True, note=body.note
    )


@router.post("/verifications/{student_id}/reject", response_model=PendingVerificationOut)
async def reject(
    student_id: uuid.UUID,
    body: RejectIn,
    request: Request,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
    notifier: NotificationClient = Depends(get_notifier),
) -> PendingVerificationOut:
    return await _review(
        session, notifier, request, principal, student_id, approve=False, note=body.note
    )


async def _review(
    session: AsyncSession,
    notifier: NotificationClient,
    request: Request,
    principal: Principal,
    student_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None,
) -> PendingVerificationOut:
    profile, user = await _student_in_tenant(session, student_id, principal.college_id)
    if profile.verification_status != VerificationStatus.PENDING_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Student is '{profile.verification_status.value}', not awaiting review",
        )
    now = datetime.now(UTC)
    docs = list(
        await session.scalars(
            select(VerificationDocument).where(
                VerificationDocument.student_id == student_id,
                VerificationDocument.status == DocumentStatus.PENDING,
            )
        )
    )
    for d in docs:
        d.status = DocumentStatus.APPROVED if approve else DocumentStatus.REJECTED
        d.reviewed_by = principal.user_id
        d.reviewed_at = now

    if approve:
        college = await session.get(College, principal.college_id)
        domain.mark_verified(profile, college, now)
        profile.review_note = note
    else:
        profile.verification_status = VerificationStatus.REJECTED
        profile.review_note = note
    record_audit(
        session,
        "verification.approved" if approve else "verification.rejected",
        college_id=principal.college_id,
        actor_id=principal.user_id,
        target_type="user",
        target_id=student_id,
        ip=client_ip(request),
        note=note,
        documents=[str(d.id) for d in docs],
    )
    await session.commit()

    if approve:
        await _notify_quietly(
            notifier,
            user.email,
            "You're verified on RideAssist",
            f"Hi {user.full_name},\n\n"
            "Your PwD verification was approved. You can now book rides.\n",
        )
    else:
        await _notify_quietly(
            notifier,
            user.email,
            "RideAssist verification update",
            f"Hi {user.full_name},\n\nYour document couldn't be approved: {note}\n"
            "You can upload a new document from the app.\n",
        )
    return PendingVerificationOut(
        student_id=profile.user_id,
        full_name=user.full_name,
        email=user.email,
        enrollment_no=profile.enrollment_no,
        verification_status=profile.verification_status,
        roster_matched=profile.roster_matched,
        registered_at=profile.created_at,
        documents=[DocumentOut.model_validate(d) for d in docs],
    )


@router.post("/roster", response_model=RosterUploadOut)
async def upload_roster(
    request: Request,
    file: UploadFile = File(
        ..., description="CSV with columns enrollment_no, full_name[, email, is_pwd]"
    ),
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> RosterUploadOut:
    """Idempotent upsert of the disability office's roster, then auto-verifies any student who
    was waiting on a document and now matches."""
    raw = await file.read(5 * 1024 * 1024)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "CSV must be UTF-8") from exc
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    if not ROSTER_COLUMNS <= headers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"CSV must contain columns: {', '.join(sorted(ROSTER_COLUMNS))}",
        )

    college = await session.get(College, principal.college_id)
    rows: dict[str, dict] = {}
    rejected: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        enrollment = row.get("enrollment_no", "").upper()
        name = row.get("full_name", "")
        if not enrollment or not name or len(enrollment) > 32:
            rejected.append(f"line {line_no}: missing/invalid enrollment_no or full_name")
            continue
        email = row.get("email", "").lower() or None
        if email and domain.email_domain(email) != college.email_domain:
            rejected.append(f"line {line_no}: email not on the college domain")
            continue
        rows[enrollment] = {
            "id": uuid.uuid4(),
            "college_id": principal.college_id,
            "enrollment_no": enrollment,
            "full_name": name[:120],
            "email": email,
            "is_pwd": row.get("is_pwd", "true").lower() in TRUTHY,
        }

    if rows:
        stmt = insert(RosterEntry).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=["college_id", "enrollment_no"],
            set_={
                "full_name": stmt.excluded.full_name,
                "email": stmt.excluded.email,
                "is_pwd": stmt.excluded.is_pwd,
                "updated_at": datetime.now(UTC),
            },
        )
        await session.execute(stmt)

    # Students who proved their inbox but weren't on the old roster get a second chance.
    waiting = (
        await session.execute(
            select(StudentProfile, User)
            .join(User, User.id == StudentProfile.user_id)
            .where(
                StudentProfile.college_id == principal.college_id,
                StudentProfile.verification_status.in_(
                    [VerificationStatus.AWAITING_DOCUMENT, VerificationStatus.PENDING_REVIEW]
                ),
                StudentProfile.enrollment_no.in_(list(rows)),
            )
        )
    ).all()
    auto_verified = 0
    for profile, user in waiting:
        if await domain.roster_match(
            session, college.id, profile.enrollment_no, user.full_name, user.email
        ):
            profile.roster_matched = True
            domain.mark_verified(profile, college)
            auto_verified += 1
            record_audit(
                session,
                "verification.auto_approved",
                college_id=college.id,
                actor_id=principal.user_id,
                target_type="user",
                target_id=user.id,
                reason="roster_upload",
            )

    record_audit(
        session,
        "roster.uploaded",
        college_id=college.id,
        actor_id=principal.user_id,
        ip=client_ip(request),
        rows=len(rows),
        rejected=len(rejected),
    )
    await session.commit()
    return RosterUploadOut(
        rows_processed=len(rows), rows_rejected=rejected[:50], auto_verified_students=auto_verified
    )


@router.post("/staff/drivers", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
async def create_driver(
    body: DriverCreateIn,
    request: Request,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> StaffOut:
    email = body.email.lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
    user = User(
        college_id=principal.college_id,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        phone=body.phone,
        role=Role.DRIVER,
    )
    session.add(user)
    await session.flush()
    session.add(DriverProfile(user_id=user.id, college_id=principal.college_id))
    record_audit(
        session,
        "staff.driver_created",
        college_id=principal.college_id,
        actor_id=principal.user_id,
        target_type="user",
        target_id=user.id,
        ip=client_ip(request),
    )
    await session.commit()
    return StaffOut.model_validate(user)


@router.get("/anomalies", response_model=list[AnomalyOut])
async def anomalies(
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    """Rule-based abuse signals (duplicate enrollment claims, OTP lockouts) for manual review."""
    return list(
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.college_id == principal.college_id,
                AuditLog.action.like("anomaly.%"),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )

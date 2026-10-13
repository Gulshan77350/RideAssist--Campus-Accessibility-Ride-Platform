import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from identity_service import domain
from identity_service.schemas import DocumentOut, VerificationStatusOut
from rideassist_common.audit import record_audit
from rideassist_common.config import get_settings
from rideassist_common.db import get_session
from rideassist_common.deps import client_ip, require_roles
from rideassist_common.models import (
    DocumentStatus,
    DocumentType,
    Role,
    StudentProfile,
    VerificationDocument,
    VerificationStatus,
)
from rideassist_common.security import Principal
from rideassist_common.storage import DocumentVault, get_vault

router = APIRouter(prefix="/api/v1/verification", tags=["auth & verification"])

# Trust the bytes, not the client-supplied Content-Type.
MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"%PDF-": "application/pdf",
}
UPLOADABLE = {
    VerificationStatus.AWAITING_DOCUMENT,
    VerificationStatus.REJECTED,
    VerificationStatus.PENDING_REVIEW,
}


def sniff_content_type(data: bytes) -> str | None:
    return next((ct for magic, ct in MAGIC_BYTES.items() if data.startswith(magic)), None)


async def latest_document(session: AsyncSession, student_id: uuid.UUID):
    return await session.scalar(
        select(VerificationDocument)
        .where(VerificationDocument.student_id == student_id)
        .order_by(VerificationDocument.uploaded_at.desc())
        .limit(1)
    )


@router.get("/status", response_model=VerificationStatusOut)
async def verification_status(
    principal: Principal = Depends(require_roles(Role.RIDER)),
    session: AsyncSession = Depends(get_session),
) -> VerificationStatusOut:
    profile = await session.get(StudentProfile, principal.user_id)
    doc = await latest_document(session, principal.user_id)
    return VerificationStatusOut(
        verification_status=profile.verification_status,
        can_book_rides=domain.is_verified_pwd(profile),
        verified_at=profile.verified_at,
        verification_expires_at=profile.verification_expires_at,
        review_note=profile.review_note,
        latest_document=DocumentOut.model_validate(doc) if doc else None,
        next_step=domain.NEXT_STEPS[profile.verification_status],
    )


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    doc_type: DocumentType = Form(...),
    file: UploadFile = File(..., description="UDID card or disability certificate (JPEG/PNG/PDF)"),
    principal: Principal = Depends(require_roles(Role.RIDER)),
    session: AsyncSession = Depends(get_session),
    vault: DocumentVault = Depends(get_vault),
) -> DocumentOut:
    profile = await session.get(StudentProfile, principal.user_id)
    if profile.verification_status not in UPLOADABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No document needed while status is '{profile.verification_status.value}'",
        )

    limit = get_settings().max_document_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File larger than 5 MB")
    content_type = sniff_content_type(data)
    if content_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only JPEG, PNG or PDF files are accepted"
        )

    doc_id = uuid.uuid4()
    key = f"{profile.college_id}/{profile.user_id}/{doc_id}.enc"
    digest = await vault.save(key, data)
    doc = VerificationDocument(
        id=doc_id,
        college_id=profile.college_id,
        student_id=profile.user_id,
        doc_type=doc_type,
        object_key=key,
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        status=DocumentStatus.PENDING,
    )
    session.add(doc)
    profile.verification_status = VerificationStatus.PENDING_REVIEW
    record_audit(
        session,
        "document.uploaded",
        college_id=profile.college_id,
        actor_id=principal.user_id,
        target_type="document",
        target_id=doc_id,
        ip=client_ip(request),
        doc_type=doc_type.value,
    )
    await session.commit()
    await session.refresh(doc)
    return DocumentOut.model_validate(doc)

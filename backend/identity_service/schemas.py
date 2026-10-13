import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from rideassist_common.models import DocumentStatus, DocumentType, Role, VerificationStatus

EnrollmentNo = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=3,
        max_length=32,
        pattern=r"^[A-Za-z0-9/_-]+$",
    ),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
# bcrypt only looks at the first 72 bytes, so cap it rather than silently truncating.
Password = Annotated[str, StringConstraints(min_length=8, max_length=72)]
OtpCode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class StudentRegisterIn(BaseModel):
    email: EmailStr
    enrollment_no: EnrollmentNo
    full_name: Name
    password: Password
    phone: Annotated[str, StringConstraints(pattern=r"^\+?\d{10,15}$")] | None = None


class RegisterOut(BaseModel):
    user_id: uuid.UUID
    verification_status: VerificationStatus
    message: str


class OtpSendIn(BaseModel):
    email: EmailStr


class OtpConfirmIn(BaseModel):
    email: EmailStr
    code: OtpCode


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int


class OtpConfirmOut(TokenOut):
    verification_status: VerificationStatus
    message: str


class RefreshIn(BaseModel):
    refresh_token: str


class StudentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enrollment_no: str
    verification_status: VerificationStatus
    verified_at: datetime | None
    verification_expires_at: datetime | None
    needs_wheelchair: bool
    pickup_notes: str | None
    emergency_contact_phone: str | None


class CampusOut(BaseModel):
    lat: float
    lng: float
    radius_m: int


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    role: Role
    college_id: uuid.UUID | None
    college_name: str | None
    campus: CampusOut | None = None
    student: StudentInfo | None = None


class AccessibilityIn(BaseModel):
    needs_wheelchair: bool | None = None
    pickup_notes: Annotated[str, StringConstraints(max_length=300)] | None = None
    emergency_contact_phone: Annotated[str, StringConstraints(pattern=r"^\+?\d{10,15}$")] | None = (
        None
    )


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: DocumentType
    content_type: str
    size_bytes: int
    status: DocumentStatus
    uploaded_at: datetime
    reviewed_at: datetime | None


class VerificationStatusOut(BaseModel):
    verification_status: VerificationStatus
    can_book_rides: bool
    verified_at: datetime | None
    verification_expires_at: datetime | None
    review_note: str | None
    latest_document: DocumentOut | None
    next_step: str


class PendingVerificationOut(BaseModel):
    student_id: uuid.UUID
    full_name: str
    email: str
    enrollment_no: str
    verification_status: VerificationStatus
    roster_matched: bool
    registered_at: datetime
    documents: list[DocumentOut]


class ReviewIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class RejectIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class RosterUploadOut(BaseModel):
    rows_processed: int
    rows_rejected: list[str]
    auto_verified_students: int


class DriverCreateIn(BaseModel):
    email: EmailStr
    full_name: Name
    phone: Annotated[str, StringConstraints(pattern=r"^\+?\d{10,15}$")]
    password: Password


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    role: Role


class CollegeCreateIn(BaseModel):
    name: Name
    email_domain: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, to_lower=True, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$"
        ),
    ]
    subdomain: Annotated[str, StringConstraints(to_lower=True, pattern=r"^[a-z0-9-]{2,63}$")]
    campus_lat: float = Field(ge=-90, le=90)
    campus_lng: float = Field(ge=-180, le=180)
    campus_radius_m: int = Field(default=3000, ge=200, le=50_000)
    reverification_days: int = Field(default=365, ge=30, le=1460)
    security_email: EmailStr | None = None
    admin_email: EmailStr
    admin_full_name: Name
    admin_password: Password


class CollegeOut(BaseModel):
    id: uuid.UUID
    name: str
    email_domain: str
    subdomain: str
    campus_lat: float
    campus_lng: float
    campus_radius_m: int
    is_active: bool


class CollegeLookupOut(BaseModel):
    name: str
    subdomain: str


class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    target_type: str | None
    target_id: str | None
    details: dict
    ip: str | None
    created_at: datetime

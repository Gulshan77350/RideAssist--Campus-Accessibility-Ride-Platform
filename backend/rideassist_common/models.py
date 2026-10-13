"""Data model. Every tenant-owned table carries ``college_id``; queries must always filter on it.

Ownership is logical: the identity service writes users/students/verification tables, the ride
service writes fleet/ride tables, but all services share one PostGIS database
(see docs/ARCHITECTURE.md).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rideassist_common.db import Base


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # Stored as VARCHAR + CHECK rather than a native PG enum so adding states is a cheap migration.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


POINT = Geography(geometry_type="POINT", srid=4326, spatial_index=False)


class Role(StrEnum):
    RIDER = "rider"
    DRIVER = "driver"
    COLLEGE_ADMIN = "college_admin"
    SUPER_ADMIN = "super_admin"


class VerificationStatus(StrEnum):
    PENDING_OTP = "pending_otp"  # registered, inbox ownership not yet proven
    AWAITING_DOCUMENT = "awaiting_document"  # inbox proven, not in PwD roster -> needs certificate
    PENDING_REVIEW = "pending_review"  # certificate uploaded, waiting for disability-cell officer
    VERIFIED = "verified"
    REJECTED = "rejected"


class DocumentType(StrEnum):
    UDID = "udid"
    COLLEGE_CERTIFICATE = "college_certificate"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VehicleKind(StrEnum):
    WHEELCHAIR_ACCESSIBLE = "wheelchair_accessible"
    STANDARD = "standard"


class RideStatus(StrEnum):
    REQUESTED = "requested"  # searching / offered to a driver
    ACCEPTED = "accepted"  # driver en route to pickup
    ONGOING = "ongoing"  # rider on board
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # no driver accepted within the search window


ACTIVE_RIDE_STATUSES = (RideStatus.REQUESTED, RideStatus.ACCEPTED, RideStatus.ONGOING)


class College(Base):
    __tablename__ = "colleges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    email_domain: Mapped[str] = mapped_column(String(120), unique=True)
    subdomain: Mapped[str] = mapped_column(String(63), unique=True)
    campus_center = mapped_column(POINT, nullable=False)
    campus_radius_m: Mapped[int] = mapped_column(Integer, default=3000)
    reverification_days: Mapped[int] = mapped_column(Integer, default=365)
    security_email: Mapped[str | None] = mapped_column(String(254))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


class RosterEntry(Base):
    """PwD roster uploaded by the college's disability office; source of truth for auto-verify."""

    __tablename__ = "roster_entries"
    __table_args__ = (UniqueConstraint("college_id", "enrollment_no"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    enrollment_no: Mapped[str] = mapped_column(String(32))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(254))
    is_pwd: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("colleges.id", ondelete="CASCADE"), index=True
    )  # NULL only for platform super admins
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


class StudentProfile(Base):
    __tablename__ = "student_profiles"
    __table_args__ = (UniqueConstraint("college_id", "enrollment_no"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    enrollment_no: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum(VerificationStatus, "verification_status"), index=True
    )
    roster_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    # accessibility preferences
    needs_wheelchair: Mapped[bool] = mapped_column(Boolean, default=False)
    pickup_notes: Mapped[str | None] = mapped_column(String(300))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _now()


class EmailOTP(Base):
    __tablename__ = "verification_otps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    otp_hash: Mapped[str] = mapped_column(String(64))  # HMAC-SHA256, never the raw code
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class VerificationDocument(Base):
    __tablename__ = "verification_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_profiles.user_id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[DocumentType] = mapped_column(_enum(DocumentType, "document_type"))
    object_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[DocumentStatus] = mapped_column(_enum(DocumentStatus, "document_status"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_at: Mapped[datetime] = _now()


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("college_id", "plate_no"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    plate_no: Mapped[str] = mapped_column(String(20))
    kind: Mapped[VehicleKind] = mapped_column(_enum(VehicleKind, "vehicle_kind"))
    capacity: Mapped[int] = mapped_column(SmallInteger, default=4)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _now()


class DriverProfile(Base):
    __tablename__ = "driver_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    college_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("colleges.id", ondelete="CASCADE"), index=True
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL"), unique=True
    )
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (
        Index("ix_rides_college_status", "college_id", "status"),
        # DB-level invariants: a rider / driver can have at most one active ride at a time.
        Index(
            "uq_rides_one_active_per_rider",
            "rider_id",
            unique=True,
            postgresql_where="status IN ('requested','accepted','ongoing')",
        ),
        Index(
            "uq_rides_one_active_per_driver",
            "driver_id",
            unique=True,
            postgresql_where="status IN ('accepted','ongoing')",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vehicles.id"))
    status: Mapped[RideStatus] = mapped_column(_enum(RideStatus, "ride_status"))
    pickup = mapped_column(POINT, nullable=False)
    dropoff = mapped_column(POINT, nullable=False)
    pickup_address: Mapped[str | None] = mapped_column(String(300))
    dropoff_address: Mapped[str | None] = mapped_column(String(300))
    needs_wheelchair: Mapped[bool] = mapped_column(Boolean, default=False)
    rider_notes: Mapped[str | None] = mapped_column(String(300))
    # matching state
    offered_driver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    # lifecycle timestamps
    requested_at: Mapped[datetime] = _now()
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))
    distance_m: Mapped[float | None] = mapped_column(Float)


class RideLocation(Base):
    """GPS trail archived from Redis once a ride finishes."""

    __tablename__ = "ride_locations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ride_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rides.id", ondelete="CASCADE"), index=True
    )
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    location = mapped_column(POINT, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("rider_rating BETWEEN 1 AND 5", name="rider_rating_range"),
        CheckConstraint("driver_rating BETWEEN 1 AND 5", name="driver_rating_range"),
    )

    ride_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rides.id", ondelete="CASCADE"), primary_key=True
    )
    college_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("colleges.id", ondelete="CASCADE"))
    rider_rating: Mapped[int | None] = mapped_column(SmallInteger)  # given BY the rider
    rider_comment: Mapped[str | None] = mapped_column(String(500))
    driver_rating: Mapped[int | None] = mapped_column(SmallInteger)  # given BY the driver
    driver_comment: Mapped[str | None] = mapped_column(String(500))


class AuditLog(Base):
    """Append-only trail of security-relevant actions (approvals, document views, anomalies)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_college_action", "college_id", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    college_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("colleges.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = _now()

"""College-admin fleet management, live operations view and the analytics overview."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import Date, case, cast, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ride_service.schemas import (
    AnalyticsOut,
    AssignVehicleIn,
    DailyCount,
    FleetDriverOut,
    RideOut,
    VehicleIn,
    VehicleOut,
    VehiclePatch,
)
from ride_service.views import ride_out
from rideassist_common.db import get_session
from rideassist_common.deps import require_roles
from rideassist_common.models import (
    ACTIVE_RIDE_STATUSES,
    DriverProfile,
    Ride,
    RideStatus,
    Role,
    StudentProfile,
    User,
    Vehicle,
    VerificationStatus,
)
from rideassist_common.redis import get_redis
from rideassist_common.security import Principal

router = APIRouter(prefix="/api/v1/admin", tags=["college admin"])
admin_only = require_roles(Role.COLLEGE_ADMIN)


@router.get("/fleet/vehicles", response_model=list[VehicleOut])
async def list_vehicles(
    principal: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)
) -> list[Vehicle]:
    return list(
        await session.scalars(
            select(Vehicle)
            .where(Vehicle.college_id == principal.college_id)
            .order_by(Vehicle.plate_no)
        )
    )


@router.post("/fleet/vehicles", response_model=VehicleOut, status_code=status.HTTP_201_CREATED)
async def add_vehicle(
    body: VehicleIn,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> Vehicle:
    vehicle = Vehicle(college_id=principal.college_id, **body.model_dump())
    session.add(vehicle)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plate number already registered") from exc
    return vehicle


@router.patch("/fleet/vehicles/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    body: VehiclePatch,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> Vehicle:
    vehicle = await session.scalar(
        select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.college_id == principal.college_id)
    )
    if vehicle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehicle not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(vehicle, field, value)
    await session.commit()
    return vehicle


@router.get("/fleet/drivers", response_model=list[FleetDriverOut])
async def list_drivers(
    principal: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)
) -> list[FleetDriverOut]:
    rows = (
        await session.execute(
            select(DriverProfile, User, Vehicle)
            .join(User, User.id == DriverProfile.user_id)
            .outerjoin(Vehicle, Vehicle.id == DriverProfile.vehicle_id)
            .where(DriverProfile.college_id == principal.college_id)
            .order_by(User.full_name)
        )
    ).all()
    return [
        FleetDriverOut(
            id=u.id,
            full_name=u.full_name,
            email=u.email,
            phone=u.phone,
            is_online=p.is_online,
            vehicle=VehicleOut.model_validate(v) if v else None,
        )
        for p, u, v in rows
    ]


@router.put("/fleet/drivers/{driver_id}/vehicle", response_model=FleetDriverOut)
async def assign_vehicle(
    driver_id: uuid.UUID,
    body: AssignVehicleIn,
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> FleetDriverOut:
    profile = await session.scalar(
        select(DriverProfile).where(
            DriverProfile.user_id == driver_id, DriverProfile.college_id == principal.college_id
        )
    )
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Driver not found")
    if profile.is_online:
        raise HTTPException(status.HTTP_409_CONFLICT, "Driver must be offline to change vehicle")
    vehicle = None
    if body.vehicle_id:
        vehicle = await session.scalar(
            select(Vehicle).where(
                Vehicle.id == body.vehicle_id, Vehicle.college_id == principal.college_id
            )
        )
        if vehicle is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehicle not found")
    profile.vehicle_id = body.vehicle_id
    try:
        await session.commit()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Vehicle is assigned to another driver"
        ) from exc
    user = await session.get(User, driver_id)
    return FleetDriverOut(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        is_online=profile.is_online,
        vehicle=VehicleOut.model_validate(vehicle) if vehicle else None,
    )


@router.get("/rides", response_model=list[RideOut])
async def live_rides(
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> list[RideOut]:
    stmt = select(Ride).where(Ride.college_id == principal.college_id)
    if active_only:
        stmt = stmt.where(Ride.status.in_(ACTIVE_RIDE_STATUSES))
    rides = await session.scalars(stmt.order_by(Ride.requested_at.desc()).limit(limit))
    return [await ride_out(session, redis, r, with_route=False) for r in rides]


@router.get("/analytics/overview", response_model=AnalyticsOut)
async def analytics_overview(
    principal: Principal = Depends(admin_only),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsOut:
    cid = principal.college_id
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    def count_if(cond):
        return func.count(case((cond, 1)))

    today_row = (
        await session.execute(
            select(
                func.count(),
                count_if(Ride.status == RideStatus.COMPLETED),
                count_if(Ride.status == RideStatus.CANCELLED),
                count_if(Ride.status == RideStatus.EXPIRED),
            ).where(Ride.college_id == cid, Ride.requested_at >= today)
        )
    ).one()
    wait_row = (
        await session.execute(
            select(
                func.avg(func.extract("epoch", Ride.accepted_at - Ride.requested_at)),
                func.avg(func.extract("epoch", Ride.started_at - Ride.accepted_at)),
            ).where(
                Ride.college_id == cid, Ride.requested_at >= week_ago, Ride.accepted_at.is_not(None)
            )
        )
    ).one()
    active_riders = await session.scalar(
        select(func.count(distinct(Ride.rider_id))).where(
            Ride.college_id == cid, Ride.requested_at >= now - timedelta(days=30)
        )
    )
    online = await session.scalar(
        select(func.count()).where(
            DriverProfile.college_id == cid, DriverProfile.is_online.is_(True)
        )
    )
    funnel_rows = (
        await session.execute(
            select(StudentProfile.verification_status, func.count())
            .where(StudentProfile.college_id == cid)
            .group_by(StudentProfile.verification_status)
        )
    ).all()
    funnel = {s.value: 0 for s in VerificationStatus} | {s.value: n for s, n in funnel_rows}

    day = cast(func.timezone("UTC", Ride.requested_at), Date)
    daily = (
        await session.execute(
            select(day, func.count(), count_if(Ride.status == RideStatus.COMPLETED))
            .where(Ride.college_id == cid, Ride.requested_at >= today - timedelta(days=13))
            .group_by(day)
            .order_by(day)
        )
    ).all()

    return AnalyticsOut(
        rides_today=today_row[0],
        completed_today=today_row[1],
        cancelled_today=today_row[2],
        unmatched_today=today_row[3],
        avg_wait_seconds_7d=round(float(wait_row[0]), 1) if wait_row[0] is not None else None,
        avg_pickup_seconds_7d=round(float(wait_row[1]), 1) if wait_row[1] is not None else None,
        active_riders_30d=active_riders or 0,
        online_drivers=online or 0,
        verification_funnel=funnel,
        rides_per_day=[DailyCount(day=d, rides=n, completed=c) for d, n, c in daily],
    )

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ride_service.schemas import DriverStatusOut, LatLng, NearbyVehicleOut, OfferOut, VehicleOut
from ride_service.views import place
from rideassist_common.db import get_session
from rideassist_common.deps import require_roles
from rideassist_common.geo import haversine_m, latlng
from rideassist_common.models import (
    DriverProfile,
    Ride,
    RideStatus,
    Role,
    User,
    Vehicle,
    VehicleKind,
)
from rideassist_common.redis import get_redis
from rideassist_common.security import Principal
from rideassist_common.tracking import (
    get_driver_position,
    nearby_drivers,
    remove_driver,
    update_driver_position,
)

router = APIRouter(prefix="/api/v1/drivers", tags=["drivers"])
driver_only = require_roles(Role.DRIVER)


async def _active_ride(session: AsyncSession, driver_id) -> Ride | None:
    return await session.scalar(
        select(Ride).where(
            Ride.driver_id == driver_id,
            Ride.status.in_([RideStatus.ACCEPTED, RideStatus.ONGOING]),
        )
    )


async def _status(session: AsyncSession, redis: Redis, profile: DriverProfile) -> DriverStatusOut:
    vehicle = await session.get(Vehicle, profile.vehicle_id) if profile.vehicle_id else None
    ride = await _active_ride(session, profile.user_id)
    pos = await get_driver_position(redis, profile.user_id) if profile.is_online else None
    return DriverStatusOut(
        is_online=profile.is_online,
        vehicle=VehicleOut.model_validate(vehicle) if vehicle else None,
        active_ride_id=ride.id if ride else None,
        position=LatLng(lat=pos["lat"], lng=pos["lng"]) if pos else None,
    )


@router.get("/me", response_model=DriverStatusOut)
async def my_status(
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> DriverStatusOut:
    return await _status(session, redis, await session.get(DriverProfile, principal.user_id))


@router.post("/me/online", response_model=DriverStatusOut)
async def go_online(
    body: LatLng,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> DriverStatusOut:
    profile = await session.get(DriverProfile, principal.user_id)
    vehicle = await session.get(Vehicle, profile.vehicle_id) if profile.vehicle_id else None
    if vehicle is None or not vehicle.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No active vehicle assigned. Ask your college admin."
        )
    profile.is_online = True
    await session.commit()
    ride = await _active_ride(session, principal.user_id)
    await update_driver_position(
        redis,
        driver_id=principal.user_id,
        college_id=principal.college_id,
        kind=vehicle.kind,
        lat=body.lat,
        lng=body.lng,
        available=ride is None,
        ride_id=ride.id if ride else None,
    )
    return await _status(session, redis, profile)


@router.post("/me/offline", response_model=DriverStatusOut)
async def go_offline(
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> DriverStatusOut:
    if await _active_ride(session, principal.user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Finish your active ride before going offline"
        )
    profile = await session.get(DriverProfile, principal.user_id)
    profile.is_online = False
    await session.commit()
    await remove_driver(redis, principal.college_id, principal.user_id)
    return await _status(session, redis, profile)


@router.post("/me/location", status_code=status.HTTP_204_NO_CONTENT)
async def report_location(
    body: LatLng,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> None:
    """HTTP heartbeat (poll-based phase). The location service's WebSocket does the same job
    at 2-3 s intervals once a ride is live; both write through the same tracking helper."""
    profile = await session.get(DriverProfile, principal.user_id)
    if not profile.is_online:
        raise HTTPException(status.HTTP_409_CONFLICT, "Go online first")
    vehicle = await session.get(Vehicle, profile.vehicle_id)
    ride = await _active_ride(session, principal.user_id)
    await update_driver_position(
        redis,
        driver_id=principal.user_id,
        college_id=principal.college_id,
        kind=vehicle.kind,
        lat=body.lat,
        lng=body.lng,
        available=ride is None,
        ride_id=ride.id if ride else None,
    )


@router.get("/me/offer", response_model=OfferOut, responses={204: {"description": "No offer"}})
async def current_offer(
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    ride = await session.scalar(
        select(Ride).where(
            Ride.college_id == principal.college_id,
            Ride.status == RideStatus.REQUESTED,
            Ride.offered_driver_id == principal.user_id,
            Ride.offer_expires_at > datetime.now(UTC),
        )
    )
    if ride is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    rider = await session.get(User, ride.rider_id)
    pos = await get_driver_position(redis, principal.user_id)
    distance = haversine_m(pos["lat"], pos["lng"], *latlng(ride.pickup)) if pos else None
    return OfferOut(
        ride_id=ride.id,
        rider_name=rider.full_name,
        pickup=place(ride.pickup, ride.pickup_address),
        dropoff=place(ride.dropoff, ride.dropoff_address),
        needs_wheelchair=ride.needs_wheelchair,
        rider_notes=ride.rider_notes,
        distance_to_pickup_m=round(distance, 1) if distance is not None else None,
        expires_at=ride.offer_expires_at,
    )


@router.get("/nearby", response_model=list[NearbyVehicleOut])
async def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius: int = Query(1500, ge=100, le=10_000, description="metres"),
    principal: Principal = Depends(require_roles(Role.RIDER, Role.COLLEGE_ADMIN)),
    redis: Redis = Depends(get_redis),
) -> list[NearbyVehicleOut]:
    """Free shuttles around a point, for the rider's map before booking (Redis GEOSEARCH)."""
    rows = await nearby_drivers(
        redis,
        college_id=principal.college_id,
        lat=lat,
        lng=lng,
        radius_m=radius,
        kinds=list(VehicleKind),
        limit=20,
    )
    out = []
    for driver_id, kind, dist in rows:
        pos = await get_driver_position(redis, driver_id)
        if pos:
            out.append(
                NearbyVehicleOut(
                    lat=pos["lat"], lng=pos["lng"], vehicle_kind=kind, distance_m=round(dist, 1)
                )
            )
    return out

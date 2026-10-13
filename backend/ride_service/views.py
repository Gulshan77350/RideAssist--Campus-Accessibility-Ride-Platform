import uuid

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ride_service.schemas import DriverInfo, LatLng, Place, RideOut, RouteInfo, VehicleOut
from rideassist_common.geo import latlng, route
from rideassist_common.models import Ride, RideStatus, Role, User, Vehicle
from rideassist_common.security import Principal
from rideassist_common.tracking import get_driver_position


async def load_ride(
    session: AsyncSession, ride_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> Ride:
    """Tenant-scoped fetch; returns 404 unless the caller is a party to the ride or its admin."""
    stmt = select(Ride).where(Ride.id == ride_id, Ride.college_id == principal.college_id)
    if lock:
        stmt = stmt.with_for_update()
    ride = await session.scalar(stmt)
    party = ride is not None and (
        principal.role == Role.COLLEGE_ADMIN
        or principal.user_id in (ride.rider_id, ride.driver_id, ride.offered_driver_id)
    )
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ride not found")
    return ride


def place(geo, address: str | None) -> Place:
    lat, lng = latlng(geo)
    return Place(lat=lat, lng=lng, address=address)


async def ride_out(
    session: AsyncSession, redis: Redis, ride: Ride, *, with_route: bool = True
) -> RideOut:
    rider = await session.get(User, ride.rider_id)
    driver_info = None
    route_info = None
    if ride.driver_id:
        driver = await session.get(User, ride.driver_id)
        vehicle = await session.get(Vehicle, ride.vehicle_id) if ride.vehicle_id else None
        pos = await get_driver_position(redis, ride.driver_id)
        driver_info = DriverInfo(
            id=driver.id,
            full_name=driver.full_name,
            phone=driver.phone,
            vehicle=VehicleOut.model_validate(vehicle) if vehicle else None,
            position=LatLng(lat=pos["lat"], lng=pos["lng"]) if pos else None,
        )
        if with_route and pos and ride.status in (RideStatus.ACCEPTED, RideStatus.ONGOING):
            target = ride.pickup if ride.status == RideStatus.ACCEPTED else ride.dropoff
            t_lat, t_lng = latlng(target)
            route_info = RouteInfo(**await route(pos["lat"], pos["lng"], t_lat, t_lng))
    return RideOut(
        id=ride.id,
        status=ride.status,
        rider_id=ride.rider_id,
        rider_name=rider.full_name,
        pickup=place(ride.pickup, ride.pickup_address),
        dropoff=place(ride.dropoff, ride.dropoff_address),
        needs_wheelchair=ride.needs_wheelchair,
        rider_notes=ride.rider_notes,
        driver=driver_info,
        route=route_info,
        requested_at=ride.requested_at,
        accepted_at=ride.accepted_at,
        started_at=ride.started_at,
        completed_at=ride.completed_at,
        cancelled_at=ride.cancelled_at,
        cancel_reason=ride.cancel_reason,
        distance_m=ride.distance_m,
    )

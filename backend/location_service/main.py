"""Live tracking over WebSockets.

Driver app  ──ws──▶ location-service ──PUBLISH ride:{id}:events──▶ Redis ──▶ every subscriber
Rider app   ◀─ws──  location-service ◀──────────── SUBSCRIBE ─────────────┘

Any number of location-service replicas can run behind the gateway: they share nothing but
Redis, so a driver and rider connected to different replicas still see each other.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from rideassist_common.app_factory import create_app
from rideassist_common.db import get_sessionmaker
from rideassist_common.deps import get_principal
from rideassist_common.geo import route
from rideassist_common.models import ACTIVE_RIDE_STATUSES, DriverProfile, Ride, RideStatus, Vehicle
from rideassist_common.redis import get_redis
from rideassist_common.security import Principal, TokenError, decode_token
from rideassist_common.tracking import (
    get_driver_position,
    publish_ride_event,
    ride_channel,
    update_driver_position,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["live tracking"])

AUTH_TIMEOUT_S = 5
MIN_UPDATE_INTERVAL_S = 1.0
TERMINAL = {RideStatus.COMPLETED.value, RideStatus.CANCELLED.value, RideStatus.EXPIRED.value}

# Application close codes (4000-4999 are reserved for apps by RFC 6455).
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_RIDE_INACTIVE = 4409


class RouteInfo(BaseModel):
    eta_seconds: int
    distance_m: float
    polyline: list[list[float]]
    provider: str


class Position(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


async def _authenticate(ws: WebSocket) -> Principal | None:
    """First-message auth: the JWT never appears in a URL, so it never lands in proxy logs."""
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_S)
        principal, _ = decode_token(str(msg.get("token", "")), "access")
        return principal
    except (TimeoutError, TokenError, ValueError, AttributeError):
        return None


@router.websocket("/track/rides/{ride_id}")
async def track_ride(ws: WebSocket, ride_id: uuid.UUID) -> None:
    """Protocol: client sends {"type":"auth","token":"<access JWT>"} first.
    Driver then streams {"lat":..,"lng":..} every 2-3 s; rider may too (shown to the driver).
    Server pushes {"type":"snapshot"|"location"|"status", ...}; closes when the ride ends."""
    await ws.accept()
    principal = await _authenticate(ws)
    if principal is None:
        await ws.close(code=CLOSE_UNAUTHORIZED, reason="unauthorized")
        return

    async with get_sessionmaker()() as session:
        ride = await session.scalar(
            select(Ride).where(Ride.id == ride_id, Ride.college_id == principal.college_id)
        )
        if ride is None or principal.user_id not in (ride.rider_id, ride.driver_id):
            await ws.close(code=CLOSE_NOT_FOUND, reason="ride not found")
            return
        if ride.status not in ACTIVE_RIDE_STATUSES:
            await ws.close(code=CLOSE_RIDE_INACTIVE, reason="ride is not active")
            return
        role = "driver" if principal.user_id == ride.driver_id else "rider"
        vehicle_kind = None
        if role == "driver":
            profile = await session.get(DriverProfile, principal.user_id)
            vehicle = await session.get(Vehicle, profile.vehicle_id) if profile.vehicle_id else None
            vehicle_kind = vehicle.kind if vehicle else None

    redis = await get_redis()
    snapshot = {
        "type": "snapshot",
        "ride_id": str(ride.id),
        "status": ride.status.value,
        "role": role,
    }
    if ride.driver_id:
        snapshot["driver"] = await get_driver_position(redis, ride.driver_id)
    await ws.send_json(snapshot)

    pubsub = redis.pubsub()
    await pubsub.subscribe(ride_channel(ride.id))

    async def outbound() -> None:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue
            event = json.loads(msg["data"])
            if event.get("type") == "location" and event.get("from") == role:
                continue  # don't echo a client's own position back to it
            await ws.send_json(event)
            if event.get("type") == "status" and event.get("status") in TERMINAL:
                return

    async def inbound() -> None:
        last = 0.0
        while True:
            try:
                pos = Position.model_validate(await ws.receive_json())
            except ValidationError:
                await ws.send_json({"type": "error", "detail": "expected {lat, lng}"})
                continue
            now = time.monotonic()
            if now - last < MIN_UPDATE_INTERVAL_S:
                continue  # client-side bugs shouldn't flood Redis
            last = now
            if role == "driver" and vehicle_kind is not None:
                await update_driver_position(
                    redis,
                    driver_id=principal.user_id,
                    college_id=principal.college_id,
                    kind=vehicle_kind,
                    lat=pos.lat,
                    lng=pos.lng,
                    available=False,
                    ride_id=ride.id,
                )
            else:
                await publish_ride_event(
                    redis,
                    ride.id,
                    {
                        "type": "location",
                        "from": role,
                        "lat": pos.lat,
                        "lng": pos.lng,
                        "ts": time.time(),
                    },
                )

    tasks = [asyncio.create_task(outbound()), asyncio.create_task(inbound())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await t
        await pubsub.unsubscribe()
        await pubsub.aclose()
        with contextlib.suppress(Exception):
            await ws.close()


@router.get("/eta", response_model=RouteInfo)
async def eta(
    from_lat: float = Query(..., ge=-90, le=90),
    from_lng: float = Query(..., ge=-180, le=180),
    to_lat: float = Query(..., ge=-90, le=90),
    to_lng: float = Query(..., ge=-180, le=180),
    _: Principal = Depends(get_principal),
) -> RouteInfo:
    """Route polyline + ETA via OSRM (falls back to a straight-line estimate)."""
    return RouteInfo(**await route(from_lat, from_lng, to_lat, to_lng))


app = create_app(
    "location",
    [router],
    description="WebSocket live location fan-out over Redis pub-sub, plus routing/ETA.",
)

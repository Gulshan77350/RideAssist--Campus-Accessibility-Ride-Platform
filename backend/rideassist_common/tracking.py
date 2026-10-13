"""Live driver state in Redis: geo index for matching, heartbeats, ride GPS trails, pub-sub.

Postgres is the system of record for rides; Redis holds the fast-changing, disposable state
(positions every 2-3 s) so the matcher never scans a SQL table to find the nearest driver.
"""

import json
import time
import uuid

from redis.asyncio import Redis

from rideassist_common.config import get_settings
from rideassist_common.models import VehicleKind

TRAIL_TTL_SECONDS = 24 * 3600


def geo_key(college_id: uuid.UUID, kind: VehicleKind | str) -> str:
    return f"geo:drivers:{college_id}:{VehicleKind(kind).value}"


def heartbeat_key(college_id: uuid.UUID) -> str:
    return f"hb:drivers:{college_id}"


def state_key(driver_id: uuid.UUID) -> str:
    return f"driver:{driver_id}:state"


def trail_key(ride_id: uuid.UUID) -> str:
    return f"ride:{ride_id}:trail"


def ride_channel(ride_id: uuid.UUID) -> str:
    return f"ride:{ride_id}:events"


def offer_lock_key(driver_id: uuid.UUID) -> str:
    return f"offer:driver:{driver_id}"


def tried_key(ride_id: uuid.UUID) -> str:
    return f"ride:{ride_id}:tried"


async def update_driver_position(
    redis: Redis,
    *,
    driver_id: uuid.UUID,
    college_id: uuid.UUID,
    kind: VehicleKind,
    lat: float,
    lng: float,
    available: bool,
    ride_id: uuid.UUID | None = None,
) -> None:
    ts = time.time()
    member = str(driver_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(
            state_key(driver_id),
            mapping={
                "college_id": str(college_id),
                "kind": kind.value,
                "lat": lat,
                "lng": lng,
                "ts": ts,
            },
        )
        pipe.zadd(heartbeat_key(college_id), {member: ts})
        if available:
            pipe.geoadd(geo_key(college_id, kind), (lng, lat, member))
        else:
            for k in VehicleKind:
                pipe.zrem(geo_key(college_id, k), member)
        if ride_id is not None:
            point = json.dumps({"lat": lat, "lng": lng, "ts": ts})
            pipe.rpush(trail_key(ride_id), point)
            pipe.expire(trail_key(ride_id), TRAIL_TTL_SECONDS)
            pipe.publish(
                ride_channel(ride_id),
                json.dumps(
                    {"type": "location", "from": "driver", "lat": lat, "lng": lng, "ts": ts}
                ),
            )
        await pipe.execute()


async def set_driver_available(redis: Redis, driver_id: uuid.UUID, available: bool) -> None:
    """Put a driver back in (or take them out of) the matching pool at their last known position."""
    state = await redis.hgetall(state_key(driver_id))
    if not state:
        return
    college_id = uuid.UUID(state["college_id"])
    member = str(driver_id)
    async with redis.pipeline(transaction=True) as pipe:
        for k in VehicleKind:
            pipe.zrem(geo_key(college_id, k), member)
        if available:
            pipe.geoadd(
                geo_key(college_id, state["kind"]),
                (float(state["lng"]), float(state["lat"]), member),
            )
        await pipe.execute()


async def remove_driver(redis: Redis, college_id: uuid.UUID, driver_id: uuid.UUID) -> None:
    member = str(driver_id)
    async with redis.pipeline(transaction=True) as pipe:
        for k in VehicleKind:
            pipe.zrem(geo_key(college_id, k), member)
        pipe.zrem(heartbeat_key(college_id), member)
        pipe.delete(state_key(driver_id))
        await pipe.execute()


async def get_driver_position(redis: Redis, driver_id: uuid.UUID) -> dict | None:
    state = await redis.hgetall(state_key(driver_id))
    if not state:
        return None
    return {"lat": float(state["lat"]), "lng": float(state["lng"]), "ts": float(state["ts"])}


async def nearby_drivers(
    redis: Redis,
    *,
    college_id: uuid.UUID,
    lat: float,
    lng: float,
    radius_m: float,
    kinds: list[VehicleKind],
    exclude: set[str] = frozenset(),
    limit: int = 10,
) -> list[tuple[str, VehicleKind, float]]:
    """Nearest available drivers (GEOSEARCH), dropping any whose heartbeat has gone stale."""
    found: list[tuple[str, VehicleKind, float]] = []
    for kind in kinds:
        rows = await redis.geosearch(
            geo_key(college_id, kind),
            longitude=lng,
            latitude=lat,
            radius=radius_m,
            unit="m",
            sort="ASC",
            count=limit + len(exclude),
            withdist=True,
        )
        found.extend((member, kind, float(dist)) for member, dist in rows if member not in exclude)
    if not found:
        return []
    cutoff = time.time() - get_settings().driver_heartbeat_ttl_seconds
    beats = await redis.zmscore(heartbeat_key(college_id), [m for m, _, _ in found])
    fresh = [row for row, beat in zip(found, beats, strict=True) if beat and beat >= cutoff]
    fresh.sort(key=lambda r: r[2])
    return fresh[:limit]


async def publish_ride_event(redis: Redis, ride_id: uuid.UUID, payload: dict) -> None:
    await redis.publish(ride_channel(ride_id), json.dumps(payload, default=str))


async def pop_trail(redis: Redis, ride_id: uuid.UUID) -> list[dict]:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lrange(trail_key(ride_id), 0, -1)
        pipe.delete(trail_key(ride_id))
        raw, _ = await pipe.execute()
    return [json.loads(p) for p in raw]

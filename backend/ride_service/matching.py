"""Dispatch: offer a ride to the nearest free driver, retry the next one on timeout/reject.

Concurrency model
- The ride row is locked (SELECT ... FOR UPDATE [SKIP LOCKED]) while its offer state changes, so
  several ride-service replicas can run the sweeper without double-dispatching.
- A short-lived Redis lock per driver (SET NX EX) stops two rides being offered to the same
  driver at once.
- Acceptance is a single conditional UPDATE, and a partial unique index guarantees a driver can
  never hold two active rides, even under a race.
- Side effects (push, pub-sub) go through an Outbox and run only after the DB commit.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rideassist_common.config import get_settings
from rideassist_common.geo import latlng
from rideassist_common.models import College, Ride, RideStatus, VehicleKind
from rideassist_common.notify import NotificationClient
from rideassist_common.tracking import (
    nearby_drivers,
    offer_lock_key,
    publish_ride_event,
    tried_key,
)

log = logging.getLogger(__name__)


class Outbox:
    """Collects side effects during a transaction; flush() after commit."""

    def __init__(self) -> None:
        self._effects: list[Callable[[], Awaitable[None]]] = []

    def add(self, fn: Callable[[], Awaitable[None]]) -> None:
        self._effects.append(fn)

    async def flush(self) -> None:
        effects, self._effects = self._effects, []
        for fn in effects:
            try:
                await fn()
            except Exception:  # noqa: BLE001 - a lost notification must not break the request
                log.exception("outbox effect failed")


def status_event(outbox: Outbox, redis: Redis, ride: Ride) -> None:
    payload = {"type": "status", "ride_id": str(ride.id), "status": ride.status.value}
    outbox.add(lambda: publish_ride_event(redis, ride.id, payload))


async def release_offer(redis: Redis, ride: Ride) -> None:
    if ride.offered_driver_id is None:
        return
    key = offer_lock_key(ride.offered_driver_id)
    if await redis.get(key) == str(ride.id):
        await redis.delete(key)


async def offer_next(
    session: AsyncSession,
    redis: Redis,
    notifier: NotificationClient,
    outbox: Outbox,
    ride: Ride,
    college: College,
) -> None:
    """Caller must hold the ride row lock. Offers to the nearest untried driver, keeps searching
    if nobody is free yet, and expires the request once the search window closes."""
    s = get_settings()
    now = datetime.now(UTC)
    await release_offer(redis, ride)
    ride.offered_driver_id = None
    ride.offer_expires_at = None

    search_over = now - ride.requested_at > timedelta(seconds=s.search_timeout_seconds)
    if ride.offer_attempts >= s.max_offer_attempts or search_over:
        ride.status = RideStatus.EXPIRED
        ride.cancel_reason = "No driver was available. Please try again."
        await redis.delete(tried_key(ride.id))
        status_event(outbox, redis, ride)
        outbox.add(
            lambda: notifier.push(
                ride.rider_id,
                "No shuttle available",
                "We couldn't find a free driver right now. Please request again.",
                {"ride_id": str(ride.id)},
            )
        )
        return

    lat, lng = latlng(ride.pickup)
    kinds = (
        [VehicleKind.WHEELCHAIR_ACCESSIBLE]
        if ride.needs_wheelchair
        else [VehicleKind.WHEELCHAIR_ACCESSIBLE, VehicleKind.STANDARD]
    )
    tried = await redis.smembers(tried_key(ride.id))
    candidates = await nearby_drivers(
        redis,
        college_id=college.id,
        lat=lat,
        lng=lng,
        radius_m=college.campus_radius_m * 2,
        kinds=kinds,
        exclude=set(tried),
    )
    for driver_id, _kind, dist in candidates:
        locked = await redis.set(
            offer_lock_key(driver_id), str(ride.id), nx=True, ex=s.offer_timeout_seconds + 5
        )
        if not locked:
            continue  # driver is currently looking at another rider's request
        ride.offered_driver_id = uuid.UUID(driver_id)
        ride.offer_expires_at = now + timedelta(seconds=s.offer_timeout_seconds)
        ride.offer_attempts += 1
        async with redis.pipeline(transaction=True) as pipe:
            pipe.sadd(tried_key(ride.id), driver_id)
            pipe.expire(tried_key(ride.id), 3600)
            await pipe.execute()
        message = f"Pickup {round(dist)} m away" + (
            " · wheelchair-accessible vehicle needed" if ride.needs_wheelchair else ""
        )
        data = {"ride_id": str(ride.id), "expires_in": s.offer_timeout_seconds}
        outbox.add(
            lambda to=ride.offered_driver_id, msg=message, data=data: notifier.push(
                to, "New ride request", msg, data
            )
        )
        log.info("ride %s offered to driver %s (%.0f m)", ride.id, driver_id, dist)
        return
    # Nobody free right now: stay REQUESTED with no offer; the sweeper retries shortly.


async def sweep_once(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, notifier: NotificationClient
) -> int:
    """Re-dispatch rides whose offer timed out or that are still waiting for a free driver."""
    outbox = Outbox()
    async with sessionmaker() as session:
        now = datetime.now(UTC)
        rides = list(
            await session.scalars(
                select(Ride)
                .where(
                    Ride.status == RideStatus.REQUESTED,
                    or_(Ride.offered_driver_id.is_(None), Ride.offer_expires_at <= now),
                )
                .order_by(Ride.requested_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        colleges: dict[uuid.UUID, College] = {}
        for ride in rides:
            if ride.college_id not in colleges:
                colleges[ride.college_id] = await session.get(College, ride.college_id)
            await offer_next(session, redis, notifier, outbox, ride, colleges[ride.college_id])
        await session.commit()
    await outbox.flush()
    return len(rides)


async def run_sweeper(
    stop: asyncio.Event,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    notifier: NotificationClient,
) -> None:
    interval = get_settings().matching_sweep_interval_seconds
    while not stop.is_set():
        try:
            await sweep_once(sessionmaker, redis, notifier)
        except Exception:  # noqa: BLE001 - keep the dispatcher alive across transient failures
            log.exception("matching sweep failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ride_service.matching import Outbox, offer_next, release_offer, status_event
from ride_service.schemas import CancelIn, RatingIn, RideOut, RideRequestIn, SosOut
from ride_service.views import load_ride, ride_out
from rideassist_common.audit import record_audit
from rideassist_common.db import get_session
from rideassist_common.deps import client_ip, get_principal, require_roles
from rideassist_common.geo import haversine_m, latlng, point
from rideassist_common.models import (
    ACTIVE_RIDE_STATUSES,
    College,
    DriverProfile,
    Rating,
    Ride,
    RideLocation,
    RideStatus,
    Role,
    StudentProfile,
    User,
)
from rideassist_common.notify import NotificationClient, NotificationError, get_notifier
from rideassist_common.policies import is_verified_pwd
from rideassist_common.redis import get_redis
from rideassist_common.security import Principal
from rideassist_common.tracking import (
    get_driver_position,
    offer_lock_key,
    pop_trail,
    set_driver_available,
    tried_key,
)

router = APIRouter(prefix="/api/v1/rides", tags=["rides"])
rider_only = require_roles(Role.RIDER)
driver_only = require_roles(Role.DRIVER)


async def _within_campus(
    session: AsyncSession, college_id: uuid.UUID, lat: float, lng: float
) -> bool:
    return bool(
        await session.scalar(
            select(
                func.ST_DWithin(College.campus_center, point(lat, lng), College.campus_radius_m)
            ).where(College.id == college_id)
        )
    )


@router.post("", response_model=RideOut, status_code=status.HTTP_201_CREATED)
async def request_ride(
    body: RideRequestIn,
    principal: Principal = Depends(rider_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> RideOut:
    profile = await session.get(StudentProfile, principal.user_id)
    if not is_verified_pwd(profile):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only verified PwD students can book rides")
    for label, p in (("Pickup", body.pickup), ("Drop", body.dropoff)):
        if not await _within_campus(session, principal.college_id, p.lat, p.lng):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{label} is outside the campus service zone"
            )

    ride = Ride(
        college_id=principal.college_id,
        rider_id=principal.user_id,
        status=RideStatus.REQUESTED,
        pickup=point(body.pickup.lat, body.pickup.lng),
        dropoff=point(body.dropoff.lat, body.dropoff.lng),
        pickup_address=body.pickup.address,
        dropoff_address=body.dropoff.address,
        needs_wheelchair=(
            body.needs_wheelchair if body.needs_wheelchair is not None else profile.needs_wheelchair
        ),
        rider_notes=body.notes or profile.pickup_notes,
    )
    session.add(ride)
    try:
        await session.flush()
    except IntegrityError as exc:  # uq_rides_one_active_per_rider
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have an active ride") from exc

    outbox = Outbox()
    college = await session.get(College, principal.college_id)
    await offer_next(session, redis, notifier, outbox, ride, college)
    await session.commit()
    await outbox.flush()
    return await ride_out(session, redis, ride, with_route=False)


@router.get("", response_model=list[RideOut])
async def my_rides(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> list[RideOut]:
    party = Ride.rider_id if principal.role == Role.RIDER else Ride.driver_id
    rides = await session.scalars(
        select(Ride)
        .where(Ride.college_id == principal.college_id, party == principal.user_id)
        .order_by(Ride.requested_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [await ride_out(session, redis, r, with_route=False) for r in rides]


@router.get("/active", response_model=RideOut, responses={204: {"description": "No active ride"}})
async def active_ride(
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    party = Ride.rider_id if principal.role == Role.RIDER else Ride.driver_id
    ride = await session.scalar(
        select(Ride).where(
            Ride.college_id == principal.college_id,
            party == principal.user_id,
            Ride.status.in_(ACTIVE_RIDE_STATUSES),
        )
    )
    if ride is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await ride_out(session, redis, ride)


@router.get("/{ride_id}", response_model=RideOut)
async def get_ride(
    ride_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RideOut:
    return await ride_out(session, redis, await load_ride(session, ride_id, principal))


# --- driver-side transitions ---------------------------------------------------------------


@router.post("/{ride_id}/accept", response_model=RideOut)
async def accept_ride(
    ride_id: uuid.UUID,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> RideOut:
    driver = await session.get(DriverProfile, principal.user_id)
    now = datetime.now(UTC)
    # One conditional UPDATE = the whole race resolution: only the driver holding a live offer wins.
    try:
        result = await session.execute(
            update(Ride)
            .where(
                Ride.id == ride_id,
                Ride.college_id == principal.college_id,
                Ride.status == RideStatus.REQUESTED,
                Ride.offered_driver_id == principal.user_id,
                Ride.offer_expires_at > now,
            )
            .values(
                status=RideStatus.ACCEPTED,
                driver_id=principal.user_id,
                vehicle_id=driver.vehicle_id,
                accepted_at=now,
                offered_driver_id=None,
                offer_expires_at=None,
            )
            .returning(Ride.id)
        )
        accepted = result.scalar_one_or_none()
    except IntegrityError as exc:  # uq_rides_one_active_per_driver
        raise HTTPException(status.HTTP_409_CONFLICT, "Finish your current ride first") from exc
    if accepted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This offer is no longer available")
    await session.commit()

    await redis.delete(offer_lock_key(principal.user_id), tried_key(ride_id))
    await set_driver_available(redis, principal.user_id, False)
    ride = await session.get(Ride, ride_id, populate_existing=True)
    outbox = Outbox()
    status_event(outbox, redis, ride)
    me = await session.get(User, principal.user_id)
    outbox.add(
        lambda: notifier.push(
            ride.rider_id,
            "Driver on the way",
            f"{me.full_name} accepted your ride",
            {"ride_id": str(ride.id)},
        )
    )
    await outbox.flush()
    return await ride_out(session, redis, ride)


@router.post("/{ride_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_offer(
    ride_id: uuid.UUID,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> None:
    ride = await load_ride(session, ride_id, principal, lock=True)
    if ride.status != RideStatus.REQUESTED or ride.offered_driver_id != principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "No pending offer for you on this ride")
    outbox = Outbox()
    await offer_next(
        session, redis, notifier, outbox, ride, await session.get(College, ride.college_id)
    )
    await session.commit()
    await outbox.flush()


@router.post("/{ride_id}/start", response_model=RideOut)
async def start_ride(
    ride_id: uuid.UUID,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RideOut:
    ride = await load_ride(session, ride_id, principal, lock=True)
    if ride.driver_id != principal.user_id or ride.status != RideStatus.ACCEPTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ride can't be started from this state")
    ride.status = RideStatus.ONGOING
    ride.started_at = datetime.now(UTC)
    await session.commit()
    outbox = Outbox()
    status_event(outbox, redis, ride)
    await outbox.flush()
    return await ride_out(session, redis, ride)


@router.post("/{ride_id}/complete", response_model=RideOut)
async def complete_ride(
    ride_id: uuid.UUID,
    principal: Principal = Depends(driver_only),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> RideOut:
    ride = await load_ride(session, ride_id, principal, lock=True)
    if ride.driver_id != principal.user_id or ride.status != RideStatus.ONGOING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ride can't be completed from this state")
    ride.status = RideStatus.COMPLETED
    ride.completed_at = datetime.now(UTC)

    # Archive the live GPS trail from Redis into Postgres for ride history.
    trail = await pop_trail(redis, ride.id)
    session.add_all(
        RideLocation(
            ride_id=ride.id,
            college_id=ride.college_id,
            location=point(p["lat"], p["lng"]),
            recorded_at=datetime.fromtimestamp(p["ts"], UTC),
        )
        for p in trail
    )
    if len(trail) >= 2:
        ride.distance_m = round(
            sum(
                haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
                for a, b in zip(trail, trail[1:], strict=False)
            ),
            1,
        )
    else:
        ride.distance_m = round(haversine_m(*latlng(ride.pickup), *latlng(ride.dropoff)), 1)
    await session.commit()

    await set_driver_available(redis, principal.user_id, True)
    outbox = Outbox()
    status_event(outbox, redis, ride)
    outbox.add(
        lambda: notifier.push(
            ride.rider_id, "You've arrived", "Tap to rate your ride", {"ride_id": str(ride.id)}
        )
    )
    await outbox.flush()
    return await ride_out(session, redis, ride, with_route=False)


@router.post("/{ride_id}/cancel", response_model=RideOut)
async def cancel_ride(
    ride_id: uuid.UUID,
    body: CancelIn,
    request: Request,
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> RideOut:
    """Rider cancel ends the ride. Driver cancel (before pickup) hands it back to dispatch."""
    ride = await load_ride(session, ride_id, principal, lock=True)
    outbox = Outbox()
    now = datetime.now(UTC)

    if principal.role == Role.RIDER and ride.rider_id == principal.user_id:
        if ride.status not in (RideStatus.REQUESTED, RideStatus.ACCEPTED):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A ride in progress can't be cancelled. Use SOS if you need help.",
            )
        await release_offer(redis, ride)
        driver_id = ride.driver_id
        ride.status = RideStatus.CANCELLED
        ride.cancelled_at = now
        ride.cancel_reason = body.reason or "Cancelled by rider"
        ride.offered_driver_id = None
        ride.offer_expires_at = None
        await session.commit()
        await redis.delete(tried_key(ride.id))
        if driver_id:
            await set_driver_available(redis, driver_id, True)
            outbox.add(
                lambda: notifier.push(
                    driver_id,
                    "Ride cancelled",
                    "The rider cancelled this ride",
                    {"ride_id": str(ride.id)},
                )
            )
    elif principal.role == Role.DRIVER and ride.driver_id == principal.user_id:
        if ride.status != RideStatus.ACCEPTED:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Only an accepted ride can be handed back"
            )
        ride.status = RideStatus.REQUESTED
        ride.driver_id = None
        ride.vehicle_id = None
        ride.accepted_at = None
        # Counted in audit so the anomaly report can surface drivers who habitually drop rides.
        record_audit(
            session,
            "anomaly.driver_cancelled",
            college_id=ride.college_id,
            actor_id=principal.user_id,
            target_type="ride",
            target_id=ride.id,
            ip=client_ip(request),
            reason=body.reason,
        )
        await redis.sadd(tried_key(ride.id), str(principal.user_id))
        await offer_next(
            session, redis, notifier, outbox, ride, await session.get(College, ride.college_id)
        )
        await session.commit()
        await set_driver_available(redis, principal.user_id, True)
        outbox.add(
            lambda: notifier.push(
                ride.rider_id,
                "Finding you another driver",
                "Your driver couldn't make it; we're reassigning your ride.",
                {"ride_id": str(ride.id)},
            )
        )
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't cancel this ride")

    status_event(outbox, redis, ride)
    await outbox.flush()
    return await ride_out(session, redis, ride, with_route=False)


@router.post("/{ride_id}/rating", status_code=status.HTTP_204_NO_CONTENT)
async def rate_ride(
    ride_id: uuid.UUID,
    body: RatingIn,
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    session: AsyncSession = Depends(get_session),
) -> None:
    ride = await load_ride(session, ride_id, principal)
    if ride.status != RideStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only completed rides can be rated")
    is_rider = principal.user_id == ride.rider_id
    if not is_rider and principal.user_id != ride.driver_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You weren't on this ride")

    rating = await session.get(Rating, ride.id, with_for_update=True) or Rating(
        ride_id=ride.id, college_id=ride.college_id
    )
    already = rating.rider_rating if is_rider else rating.driver_rating
    if already is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already rated this ride")
    if is_rider:
        rating.rider_rating, rating.rider_comment = body.rating, body.comment
    else:
        rating.driver_rating, rating.driver_comment = body.rating, body.comment
    session.add(rating)
    await session.commit()


@router.post("/{ride_id}/sos", response_model=SosOut, status_code=status.HTTP_202_ACCEPTED)
async def sos(
    ride_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    notifier: NotificationClient = Depends(get_notifier),
) -> SosOut:
    """Panic button: alerts campus security + college admins with the live location,
    independently of the normal ride flow (works in any non-terminal state)."""
    ride = await load_ride(session, ride_id, principal)
    if principal.user_id not in (ride.rider_id, ride.driver_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You aren't on this ride")
    if ride.status not in ACTIVE_RIDE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ride isn't active")

    pos = await get_driver_position(redis, ride.driver_id) if ride.driver_id else None
    lat, lng = (pos["lat"], pos["lng"]) if pos else latlng(ride.pickup)
    caller = await session.get(User, principal.user_id)
    college = await session.get(College, ride.college_id)
    admins = list(
        await session.scalars(
            select(User.email).where(
                User.college_id == ride.college_id,
                User.role == Role.COLLEGE_ADMIN,
                User.is_active.is_(True),
            )
        )
    )
    recipients = [e for e in [college.security_email, *admins] if e]
    record_audit(
        session,
        "ride.sos",
        college_id=ride.college_id,
        actor_id=principal.user_id,
        target_type="ride",
        target_id=ride.id,
        ip=client_ip(request),
        lat=lat,
        lng=lng,
    )
    await session.commit()

    rider_profile = await session.get(StudentProfile, ride.rider_id)
    emergency = rider_profile.emergency_contact_phone if rider_profile else None
    body = (
        f"SOS raised by {caller.full_name} ({principal.role.value}, "
        f"phone {caller.phone or 'n/a'})\n"
        f"Ride {ride.id} — status {ride.status.value}\n"
        f"Rider's emergency contact: {emergency or 'not provided'}\n"
        f"Last known location: https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=18/{lat}/{lng}\n"
    )
    alerted = []
    for to in recipients:
        try:
            await notifier.send_email(to, f"[SOS] RideAssist emergency — {college.name}", body)
            alerted.append(to)
        except NotificationError:
            continue
    other_party = ride.driver_id if principal.user_id == ride.rider_id else ride.rider_id
    if other_party:
        await notifier.push(
            other_party, "SOS raised", "Campus security has been alerted", {"ride_id": str(ride.id)}
        )
    return SosOut(
        alerted=alerted,
        message="Campus security has been alerted with your live location."
        if alerted
        else "Alert recorded; contact campus security directly as well.",
    )

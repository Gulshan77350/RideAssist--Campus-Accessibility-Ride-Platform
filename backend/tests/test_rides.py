import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from ride_service.matching import sweep_once
from rideassist_common.db import get_sessionmaker
from rideassist_common.models import Ride
from rideassist_common.redis import get_redis
from tests.conftest import auth_headers, register_and_confirm

PICKUP = {"lat": 25.4310, "lng": 81.7720, "address": "Hostel BH-3"}
DROP = {"lat": 25.4290, "lng": 81.7700, "address": "Library"}
WA, STD = "wheelchair_accessible", "standard"


async def driver_online(rides, identity, email, lat, lng) -> dict:
    headers = await auth_headers(identity, email)
    r = await rides.post(
        "/api/v1/drivers/me/online", headers=headers, json={"lat": lat, "lng": lng}
    )
    assert r.status_code == 200, r.text
    return headers


async def request(rides, rider, **overrides):
    return await rides.post(
        "/api/v1/rides", headers=rider, json={"pickup": PICKUP, "dropoff": DROP, **overrides}
    )


async def test_full_ride_lifecycle(rides, identity, tenant, verified_rider, notifier):
    near = await driver_online(rides, identity, tenant.drivers[STD], 25.4312, 81.7722)
    far = await driver_online(rides, identity, tenant.drivers[WA], 25.4350, 81.7780)

    r = await request(rides, verified_rider, notes="Ramp needed at gate")
    assert r.status_code == 201, r.text
    ride_id = r.json()["id"]

    # Nearest free driver gets the offer; the other sees nothing.
    offer = await rides.get("/api/v1/drivers/me/offer", headers=near)
    assert offer.status_code == 200 and offer.json()["rider_notes"] == "Ramp needed at gate"
    assert (await rides.get("/api/v1/drivers/me/offer", headers=far)).status_code == 204

    # Someone without the offer can't steal the ride.
    assert (await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=far)).status_code == 409

    acc = await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=near)
    assert acc.status_code == 200
    body = acc.json()
    assert body["status"] == "accepted" and body["driver"]["vehicle"]["kind"] == STD
    assert body["route"]["eta_seconds"] >= 0

    # Rider sees the assigned driver + ETA.
    seen = (await rides.get(f"/api/v1/rides/{ride_id}", headers=verified_rider)).json()
    assert seen["driver"]["full_name"] == "Driver 1"

    for lat, lng in [(25.4308, 81.7718), (25.4300, 81.7710)]:
        assert (
            await rides.post(
                "/api/v1/drivers/me/location", headers=near, json={"lat": lat, "lng": lng}
            )
        ).status_code == 204

    assert (await rides.post(f"/api/v1/rides/{ride_id}/start", headers=near)).json()[
        "status"
    ] == "ongoing"
    await rides.post(
        "/api/v1/drivers/me/location", headers=near, json={"lat": 25.4290, "lng": 81.7700}
    )
    done = await rides.post(f"/api/v1/rides/{ride_id}/complete", headers=near)
    assert done.json()["status"] == "completed"
    assert done.json()["distance_m"] > 100  # computed from the archived GPS trail

    assert (
        await rides.post(
            f"/api/v1/rides/{ride_id}/rating",
            headers=verified_rider,
            json={"rating": 5, "comment": "Very patient"},
        )
    ).status_code == 204
    assert (
        await rides.post(
            f"/api/v1/rides/{ride_id}/rating", headers=verified_rider, json={"rating": 4}
        )
    ).status_code == 409

    history = (await rides.get("/api/v1/rides", headers=verified_rider)).json()
    assert [h["status"] for h in history] == ["completed"]
    assert any(p["title"] == "Driver on the way" for p in notifier.pushes)


async def test_unverified_student_cannot_book(rides, identity, notifier, tenant):
    body = await register_and_confirm(identity, notifier, "nope@iiita.ac.in", "IIT0000", "No Body")
    assert body["verification_status"] == "awaiting_document"
    r = await request(rides, {"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 403


async def test_pickup_outside_campus_rejected(rides, verified_rider):
    r = await request(rides, verified_rider, pickup={"lat": 28.6139, "lng": 77.2090})
    assert r.status_code == 422


async def test_only_one_active_ride_per_rider(rides, verified_rider):
    assert (await request(rides, verified_rider)).status_code == 201
    assert (await request(rides, verified_rider)).status_code == 409


async def test_wheelchair_request_skips_standard_vehicles(rides, identity, tenant, verified_rider):
    std = await driver_online(rides, identity, tenant.drivers[STD], 25.4310, 81.7720)  # right there
    wa = await driver_online(rides, identity, tenant.drivers[WA], 25.4360, 81.7790)  # further away
    r = await request(rides, verified_rider, needs_wheelchair=True)
    assert r.status_code == 201
    assert (await rides.get("/api/v1/drivers/me/offer", headers=std)).status_code == 204
    assert (await rides.get("/api/v1/drivers/me/offer", headers=wa)).status_code == 200


async def test_offer_timeout_moves_to_next_driver(
    rides, identity, tenant, verified_rider, notifier
):
    first = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    second = await driver_online(rides, identity, tenant.drivers[WA], 25.4330, 81.7750)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    assert (await rides.get("/api/v1/drivers/me/offer", headers=first)).status_code == 200

    async with get_sessionmaker()() as s:  # simulate the 15 s window elapsing
        await s.execute(
            update(Ride).values(offer_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    assert await sweep_once(get_sessionmaker(), await get_redis(), notifier) == 1

    assert (await rides.get("/api/v1/drivers/me/offer", headers=first)).status_code == 204
    offer = await rides.get("/api/v1/drivers/me/offer", headers=second)
    assert offer.json()["ride_id"] == ride_id
    # The timed-out driver can no longer accept.
    assert (await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=first)).status_code == 409


async def test_reject_offers_next_then_expires(rides, identity, tenant, verified_rider, notifier):
    only = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    assert (await rides.post(f"/api/v1/rides/{ride_id}/reject", headers=only)).status_code == 204
    ride = (await rides.get(f"/api/v1/rides/{ride_id}", headers=verified_rider)).json()
    assert ride["status"] == "requested"  # still searching; nobody else free yet

    async with get_sessionmaker()() as s:  # search window over
        await s.execute(update(Ride).values(requested_at=datetime.now(UTC) - timedelta(minutes=5)))
        await s.commit()
    await sweep_once(get_sessionmaker(), await get_redis(), notifier)
    ride = (await rides.get(f"/api/v1/rides/{ride_id}", headers=verified_rider)).json()
    assert ride["status"] == "expired"


async def test_concurrent_accepts_only_one_wins(rides, identity, tenant, verified_rider):
    driver = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    results = await asyncio.gather(
        *[rides.post(f"/api/v1/rides/{ride_id}/accept", headers=driver) for _ in range(5)]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409]


async def test_driver_cancel_hands_ride_back(rides, identity, tenant, verified_rider):
    a = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    b = await driver_online(rides, identity, tenant.drivers[WA], 25.4330, 81.7750)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=a)
    r = await rides.post(f"/api/v1/rides/{ride_id}/cancel", headers=a, json={"reason": "flat tyre"})
    assert r.json()["status"] == "requested"
    assert (await rides.get("/api/v1/drivers/me/offer", headers=b)).json()["ride_id"] == ride_id


async def test_rider_cancel_frees_driver(rides, identity, tenant, verified_rider):
    d = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=d)
    r = await rides.post(f"/api/v1/rides/{ride_id}/cancel", headers=verified_rider, json={})
    assert r.json()["status"] == "cancelled"
    nearby = await rides.get(
        "/api/v1/drivers/nearby", headers=verified_rider, params={"lat": 25.431, "lng": 81.772}
    )
    assert len(nearby.json()) == 1 and "id" not in nearby.json()[0]  # anonymised


async def test_sos_alerts_security(rides, identity, tenant, verified_rider, notifier):
    d = await driver_online(rides, identity, tenant.drivers[STD], 25.4311, 81.7721)
    ride_id = (await request(rides, verified_rider)).json()["id"]
    await rides.post(f"/api/v1/rides/{ride_id}/accept", headers=d)
    r = await rides.post(f"/api/v1/rides/{ride_id}/sos", headers=verified_rider)
    assert r.status_code == 202
    assert "security@iiita.ac.in" in r.json()["alerted"]
    assert any(m["subject"].startswith("[SOS]") for m in notifier.emails)


async def test_other_riders_cannot_see_ride(rides, identity, notifier, tenant, verified_rider):
    ride_id = (await request(rides, verified_rider)).json()["id"]
    other = await register_and_confirm(identity, notifier, "o@iiita.ac.in", "IIT0101", "Other Kid")
    r = await rides.get(
        f"/api/v1/rides/{ride_id}", headers={"Authorization": f"Bearer {other['access_token']}"}
    )
    assert r.status_code == 404


async def test_admin_analytics(rides, identity, tenant, verified_rider):
    await request(rides, verified_rider)
    admin = await auth_headers(identity, tenant.admin_email)
    r = await rides.get("/api/v1/admin/analytics/overview", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rides_today"] == 1
    assert body["verification_funnel"]["verified"] == 1
    assert len(body["rides_per_day"]) == 1


async def test_driver_needs_vehicle_to_go_online(rides, identity, tenant):
    admin = await auth_headers(identity, tenant.admin_email)
    created = await identity.post(
        "/api/v1/admin/staff/drivers",
        headers=admin,
        json={
            "email": "new@iiita.ac.in",
            "full_name": "New Driver",
            "phone": "+919811111111",
            "password": "Passw0rd!23",
        },
    )
    assert created.status_code == 201
    headers = await auth_headers(identity, "new@iiita.ac.in")
    r = await rides.post(
        "/api/v1/drivers/me/online", headers=headers, json={"lat": 25.43, "lng": 81.77}
    )
    assert r.status_code == 409

    vehicle = await rides.post(
        "/api/v1/admin/fleet/vehicles", headers=admin, json={"plate_no": "UP70AB1234", "kind": WA}
    )
    await rides.put(
        f"/api/v1/admin/fleet/drivers/{created.json()['id']}/vehicle",
        headers=admin,
        json={"vehicle_id": vehicle.json()["id"]},
    )
    r = await rides.post(
        "/api/v1/drivers/me/online", headers=headers, json={"lat": 25.43, "lng": 81.77}
    )
    assert r.status_code == 200 and r.json()["is_online"] is True

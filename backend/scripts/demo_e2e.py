"""End-to-end demo against the running stack, through the Nginx gateway.

    docker compose run --rm --no-deps migrate python -m scripts.demo_e2e

Student signs up -> OTP read from Mailpit -> auto-verified via roster -> books a wheelchair ride
-> nearest accessible driver gets the offer -> accepts -> both connect over WebSocket -> driver
streams GPS -> rider sees it live -> start -> complete -> rating -> admin analytics.
"""

import asyncio
import json
import os
import re

import httpx
import websockets

API = os.getenv("DEMO_API", "http://gateway")
WS = API.replace("http", "ws", 1)
MAILPIT = os.getenv("DEMO_MAILPIT", "http://mailpit:8025")
STUDENT = ("iit2023002@iiita.ac.in", "IIT2023002", "Priya Verma", "Student@123")
DRIVERS = {
    "ramesh.driver@iiita.ac.in": (25.4320, 81.7730),
    "suresh.driver@iiita.ac.in": (25.4306, 81.7716),
}
PICKUP = {"lat": 25.4300, "lng": 81.7710, "address": "Girls Hostel 1, ramp side"}
DROP = {"lat": 25.4285, "lng": 81.7695, "address": "Admin Block"}


def step(msg: str) -> None:
    print(f"\n\033[1;36m▶ {msg}\033[0m")


async def latest_otp(client: httpx.AsyncClient, email: str) -> str:
    for _ in range(20):
        found = (
            await client.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{email}"})
        ).json()
        if found.get("messages"):
            msg = (
                await client.get(f"{MAILPIT}/api/v1/message/{found['messages'][0]['ID']}")
            ).json()
            return re.search(r"\b(\d{6})\b", msg["Text"]).group(1)
        await asyncio.sleep(0.5)
    raise RuntimeError("OTP email never arrived")


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    r = await client.post(
        f"{API}/api/v1/auth/token", data={"username": email, "password": password}
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        email, enrollment, name, password = STUDENT
        step(f"Student registers with college email {email}")
        r = await c.post(
            f"{API}/api/v1/auth/register/student",
            json={
                "email": email,
                "enrollment_no": enrollment,
                "full_name": name,
                "password": password,
            },
        )
        if r.status_code == 201:
            print("  ", r.json()["message"], "| status:", r.json()["verification_status"])
            code = await latest_otp(c, email)
            step(f"OTP {code} read from Mailpit, confirming")
            r = await c.post(
                f"{API}/api/v1/verification/otp/confirm", json={"email": email, "code": code}
            )
            r.raise_for_status()
            print("   status:", r.json()["verification_status"], "-", r.json()["message"])
            rider = {"Authorization": f"Bearer {r.json()['access_token']}"}
        else:
            print("   already registered, logging in")
            rider = await login(c, email, password)
        print("   request id echoed by gateway:", r.headers.get("x-request-id"))

        stale = await c.get(f"{API}/api/v1/rides/active", headers=rider)
        if stale.status_code == 200:  # leftover from an interrupted earlier run
            await c.post(f"{API}/api/v1/rides/{stale.json()['id']}/cancel", headers=rider, json={})

        step("Drivers go online")
        drivers = {}
        for d_email, (lat, lng) in DRIVERS.items():
            drivers[d_email] = await login(c, d_email, "Driver@12345")
            r = await c.post(
                f"{API}/api/v1/drivers/me/online",
                headers=drivers[d_email],
                json={"lat": lat, "lng": lng},
            )
            print(f"   {d_email}: online, vehicle {r.json()['vehicle']['kind']}")

        nearby = (
            await c.get(
                f"{API}/api/v1/drivers/nearby", headers=rider, params=PICKUP | {"radius": 2000}
            )
        ).json()
        print(f"   rider map shows {len(nearby)} free shuttles nearby (anonymised)")

        step("Rider books a wheelchair-accessible ride")
        r = await c.post(
            f"{API}/api/v1/rides",
            headers=rider,
            json={
                "pickup": PICKUP,
                "dropoff": DROP,
                "needs_wheelchair": True,
                "notes": "Ramp needed",
            },
        )
        r.raise_for_status()
        ride_id = r.json()["id"]
        print("   ride", ride_id, "status:", r.json()["status"])

        driver_email = None
        for d_email, headers in drivers.items():
            if (await c.get(f"{API}/api/v1/drivers/me/offer", headers=headers)).status_code == 200:
                driver_email = d_email
        driver = drivers[driver_email]
        print(
            f"   offer went to {driver_email} "
            "(the accessible vehicle, even though another is closer)"
        )
        r = await c.post(f"{API}/api/v1/rides/{ride_id}/accept", headers=driver)
        r.raise_for_status()
        print("   accepted; ETA to pickup:", r.json()["route"]["eta_seconds"], "s")

        step("Live tracking over WebSocket (driver streams GPS, rider receives it)")
        rider_token = rider["Authorization"].split()[1]
        driver_token = driver["Authorization"].split()[1]
        received: list[dict] = []

        async def rider_ws() -> None:
            async with websockets.connect(f"{WS}/api/v1/track/rides/{ride_id}") as ws:
                await ws.send(json.dumps({"type": "auth", "token": rider_token}))
                async for raw in ws:
                    event = json.loads(raw)
                    received.append(event)
                    print("   rider ◀", event)
                    if event.get("type") == "status" and event.get("status") == "completed":
                        return

        rider_task = asyncio.create_task(rider_ws())
        await asyncio.sleep(0.5)
        async with websockets.connect(f"{WS}/api/v1/track/rides/{ride_id}") as ws:
            await ws.send(json.dumps({"type": "auth", "token": driver_token}))
            print("   driver ◀", json.loads(await ws.recv()))
            for lat, lng in [(25.4315, 81.7725), (25.4308, 81.7718), (25.4301, 81.7711)]:
                await ws.send(json.dumps({"lat": lat, "lng": lng}))
                await asyncio.sleep(1.1)

            step("Driver starts and completes the ride")
            (await c.post(f"{API}/api/v1/rides/{ride_id}/start", headers=driver)).raise_for_status()
            for lat, lng in [(25.4295, 81.7705), (25.4286, 81.7696)]:
                await ws.send(json.dumps({"lat": lat, "lng": lng}))
                await asyncio.sleep(1.1)
            r = await c.post(f"{API}/api/v1/rides/{ride_id}/complete", headers=driver)
            r.raise_for_status()
            print("   completed; distance from archived GPS trail:", r.json()["distance_m"], "m")
        await asyncio.wait_for(rider_task, timeout=10)
        locations = sum(1 for e in received if e.get("type") == "location")
        print(f"   rider received {locations} live location updates")

        await c.post(
            f"{API}/api/v1/rides/{ride_id}/rating",
            headers=rider,
            json={"rating": 5, "comment": "Kind and careful"},
        )
        for h in drivers.values():
            await c.post(f"{API}/api/v1/drivers/me/offline", headers=h)

        step("College admin analytics")
        admin = await login(c, "admin@iiita.ac.in", "Admin@12345")
        print(
            json.dumps(
                (await c.get(f"{API}/api/v1/admin/analytics/overview", headers=admin)).json(),
                indent=2,
            )
        )
        print("\n\033[1;32m✔ end-to-end flow succeeded\033[0m")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ride_service.matching import run_sweeper
from ride_service.routers import drivers, fleet, rides
from rideassist_common.app_factory import create_app
from rideassist_common.db import get_sessionmaker
from rideassist_common.notify import get_notifier
from rideassist_common.redis import get_redis


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_sweeper(stop, get_sessionmaker(), await get_redis(), get_notifier())
    )
    yield
    stop.set()
    await task


app = create_app(
    "rides",
    [rides.router, drivers.router, fleet.router],
    lifespan=lifespan,
    description=(
        "Ride requests, Redis-geo driver matching with timed offers, the ride state machine, "
        "fleet management and analytics."
    ),
)

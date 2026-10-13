"""Nearest-driver lookup benchmark: Redis GEOSEARCH vs PostGIS (GiST) vs a naive SQL scan.

    docker compose run --rm --no-deps migrate python -m scripts.bench_geo [N]

Uses a TEMP table and a throwaway Redis key, so it never touches real data.
"""

import asyncio
import random
import statistics
import sys
import time

from sqlalchemy import text

from rideassist_common.db import get_engine
from rideassist_common.redis import get_redis

LAT, LNG = 25.4304, 81.7713
QUERIES = 300
RADIUS_M = 1500
KEY = "bench:geo:drivers"


def pct(samples: list[float], p: float) -> float:
    return statistics.quantiles(samples, n=100)[int(p) - 1] if len(samples) > 1 else samples[0]


def summary(name: str, samples: list[float]) -> str:
    return (
        f"{name:<28} p50 {statistics.median(samples):7.3f} ms   "
        f"p95 {pct(samples, 95):7.3f} ms   p99 {pct(samples, 99):7.3f} ms"
    )


def probe() -> tuple[float, float]:
    return LAT + random.uniform(-0.02, 0.02), LNG + random.uniform(-0.02, 0.02)


async def main(n: int) -> None:
    random.seed(7)
    points = [
        (i, LAT + random.uniform(-0.1, 0.1), LNG + random.uniform(-0.1, 0.1)) for i in range(n)
    ]

    redis = await get_redis()
    await redis.delete(KEY)
    for i in range(0, n, 5000):
        await redis.geoadd(
            KEY, [v for _, lat, lng in points[i : i + 5000] for v in (lng, lat, f"d{_}")]
        )

    redis_ms = []
    for _ in range(QUERIES):
        lat, lng = probe()
        t = time.perf_counter()
        await redis.geosearch(
            KEY,
            longitude=lng,
            latitude=lat,
            radius=RADIUS_M,
            unit="m",
            sort="ASC",
            count=10,
            withdist=True,
        )
        redis_ms.append((time.perf_counter() - t) * 1000)
    await redis.delete(KEY)

    query = text(
        "SELECT id, ST_Distance(loc, ST_MakePoint(:lng, :lat)::geography) AS d FROM {t} "
        "WHERE ST_DWithin(loc, ST_MakePoint(:lng, :lat)::geography, :r) ORDER BY d LIMIT 10"
    )
    naive_query = text(
        "SELECT id, ST_Distance(loc, ST_MakePoint(:lng, :lat)::geography) AS d FROM {t} "
        "ORDER BY d LIMIT 10"
    )
    results = {}
    async with get_engine().connect() as conn:
        for table, indexed in (("bench_plain", False), ("bench_gist", True)):
            await conn.execute(
                text(f"CREATE TEMP TABLE {table} (id int, loc geography(Point,4326))")
            )
            await conn.execute(
                text(
                    f"INSERT INTO {table} SELECT g, ST_MakePoint("  # noqa: S608 - constant name
                    f":lng + (random()*0.2 - 0.1), :lat + (random()*0.2 - 0.1))::geography "
                    "FROM generate_series(1, :n) g"
                ),
                {"lat": LAT, "lng": LNG, "n": n},
            )
            if indexed:
                await conn.execute(text(f"CREATE INDEX ON {table} USING gist (loc)"))
            await conn.execute(text(f"ANALYZE {table}"))

        for name, table, q in (
            ("naive SQL scan (no index)", "bench_plain", naive_query),
            ("PostGIS ST_DWithin + GiST", "bench_gist", query),
        ):
            samples = []
            for _ in range(QUERIES // 3 if table == "bench_plain" else QUERIES):
                lat, lng = probe()
                t = time.perf_counter()
                await conn.execute(
                    text(q.text.format(t=table)), {"lat": lat, "lng": lng, "r": RADIUS_M}
                )
                samples.append((time.perf_counter() - t) * 1000)
            results[name] = samples
        await conn.rollback()

    print(f"\nNearest-10 drivers within {RADIUS_M} m, {n:,} drivers, client-side latency\n")
    for name, samples in results.items():
        print(summary(name, samples))
    print(summary("Redis GEOSEARCH", redis_ms))
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 50_000))

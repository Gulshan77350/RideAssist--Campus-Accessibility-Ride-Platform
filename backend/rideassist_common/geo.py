import logging
import math

import httpx
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point

from rideassist_common.config import get_settings

log = logging.getLogger(__name__)
EARTH_RADIUS_M = 6_371_000


def point(lat: float, lng: float):
    return from_shape(Point(lng, lat), srid=4326)


def latlng(geo) -> tuple[float, float]:
    p = to_shape(geo)
    return p.y, p.x


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


async def route(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> dict:
    """ETA + polyline. Uses OSRM when configured, otherwise a straight-line estimate.

    Campus roads are short and slow, so the fallback multiplies straight-line distance by a
    detour factor of 1.3 before applying the average campus speed.
    """
    s = get_settings()
    if s.osrm_url:
        url = (
            f"{s.osrm_url.rstrip('/')}/route/v1/driving/"
            f"{from_lng},{from_lat};{to_lng},{to_lat}?overview=full&geometries=geojson"
        )
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            best = resp.json()["routes"][0]
            return {
                "distance_m": round(best["distance"], 1),
                "eta_seconds": int(best["duration"]),
                "polyline": [[lat, lng] for lng, lat in best["geometry"]["coordinates"]],
                "provider": "osrm",
            }
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("OSRM routing failed, falling back to straight line: %s", exc)

    distance = haversine_m(from_lat, from_lng, to_lat, to_lng) * 1.3
    speed_ms = s.avg_campus_speed_kmh * 1000 / 3600
    return {
        "distance_m": round(distance, 1),
        "eta_seconds": int(distance / speed_ms),
        "polyline": [[from_lat, from_lng], [to_lat, to_lng]],
        "provider": "straight_line",
    }

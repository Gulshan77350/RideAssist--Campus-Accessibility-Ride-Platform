import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rideassist_common.models import RideStatus, VehicleKind

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Place(LatLng):
    address: Note | None = None


class RideRequestIn(BaseModel):
    pickup: Place
    dropoff: Place
    needs_wheelchair: bool | None = Field(
        None, description="Defaults to the rider's saved accessibility preference"
    )
    notes: Note | None = Field(None, description='e.g. "ramp needed at hostel gate"')


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plate_no: str
    kind: VehicleKind
    capacity: int
    is_active: bool


class DriverInfo(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str | None
    vehicle: VehicleOut | None
    position: LatLng | None


class RouteInfo(BaseModel):
    eta_seconds: int
    distance_m: float
    polyline: list[list[float]]
    provider: str


class RideOut(BaseModel):
    id: uuid.UUID
    status: RideStatus
    rider_id: uuid.UUID
    rider_name: str
    pickup: Place
    dropoff: Place
    needs_wheelchair: bool
    rider_notes: str | None
    driver: DriverInfo | None
    route: RouteInfo | None = Field(None, description="Driver→pickup, or driver→drop when ongoing")
    requested_at: datetime
    accepted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    distance_m: float | None


class OfferOut(BaseModel):
    ride_id: uuid.UUID
    rider_name: str
    pickup: Place
    dropoff: Place
    needs_wheelchair: bool
    rider_notes: str | None
    distance_to_pickup_m: float | None
    expires_at: datetime


class CancelIn(BaseModel):
    reason: Note | None = None


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class SosOut(BaseModel):
    alerted: list[str]
    message: str


class DriverStatusOut(BaseModel):
    is_online: bool
    vehicle: VehicleOut | None
    active_ride_id: uuid.UUID | None
    position: LatLng | None


class NearbyVehicleOut(BaseModel):
    """Anonymised: riders see where shuttles are, not who is driving them."""

    lat: float
    lng: float
    vehicle_kind: VehicleKind
    distance_m: float


class VehicleIn(BaseModel):
    plate_no: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=4, max_length=20)
    ]
    kind: VehicleKind
    capacity: int = Field(4, ge=1, le=60)


class VehiclePatch(BaseModel):
    kind: VehicleKind | None = None
    capacity: int | None = Field(None, ge=1, le=60)
    is_active: bool | None = None


class AssignVehicleIn(BaseModel):
    vehicle_id: uuid.UUID | None


class FleetDriverOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    is_online: bool
    vehicle: VehicleOut | None


class DailyCount(BaseModel):
    day: date
    rides: int
    completed: int


class AnalyticsOut(BaseModel):
    rides_today: int
    completed_today: int
    cancelled_today: int
    unmatched_today: int
    avg_wait_seconds_7d: float | None = Field(description="request → driver accepted")
    avg_pickup_seconds_7d: float | None = Field(description="accepted → rider on board")
    active_riders_30d: int
    online_drivers: int
    verification_funnel: dict[str, int]
    rides_per_day: list[DailyCount]

import uuid

import pytest

from identity_service.domain import names_match, normalize_name
from identity_service.routers.verification import sniff_content_type
from rideassist_common.geo import haversine_m, route
from rideassist_common.models import Role
from rideassist_common.security import (
    Principal,
    TokenError,
    create_token_pair,
    decode_token,
    hash_otp,
    otp_matches,
)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Gulshan Kumar", "gulshan kumar", True),
        ("Gulshan Kumar", "Kumar Gulshan", True),
        ("Aarav Sharma", "Aarav Sharmaa", True),  # small typo tolerated
        ("Aarav Sharma", "Rohan Gupta", False),
        ("", "Someone", False),
    ],
)
def test_names_match(a, b, expected):
    assert names_match(a, b) is expected


def test_normalize_strips_accents_and_punctuation():
    assert normalize_name("  Zoë  O'Brien ") == "brien o zoe"


def test_otp_hash_is_bound_to_user():
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    h = hash_otp(u1, "123456")
    assert otp_matches(u1, "123456", h)
    assert not otp_matches(u2, "123456", h)
    assert not otp_matches(u1, "123457", h)


def test_token_types_are_not_interchangeable():
    p = Principal(uuid.uuid4(), Role.RIDER, uuid.uuid4())
    pair = create_token_pair(p)
    assert decode_token(pair["access_token"], "access")[0] == p
    with pytest.raises(TokenError):
        decode_token(pair["refresh_token"], "access")


def test_sniff_content_type():
    assert sniff_content_type(b"%PDF-1.7 ...") == "application/pdf"
    assert sniff_content_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert sniff_content_type(b"<html>") is None


def test_haversine_known_distance():
    # ~1.11 km per 0.01 degree of latitude
    assert haversine_m(25.0, 81.0, 25.01, 81.0) == pytest.approx(1112, rel=0.01)


async def test_route_fallback_without_osrm():
    r = await route(25.4304, 81.7713, 25.4404, 81.7713)
    assert r["provider"] == "straight_line"
    assert r["eta_seconds"] > 0 and len(r["polyline"]) == 2


def test_refuses_dev_secrets_in_production():
    from pydantic import ValidationError

    from rideassist_common.config import Settings

    with pytest.raises(ValidationError, match="document_encryption_key"):
        Settings(
            environment="production",
            jwt_secret="x" * 40,
            otp_secret="y" * 40,
            internal_api_key="z" * 40,
        )

from identity_service.routers import admin, auth, colleges, verification
from rideassist_common.app_factory import create_app

app = create_app(
    "identity",
    [auth.router, verification.router, admin.router, colleges.router],
    description=(
        "Accounts, JWT auth and the PwD verification pipeline: roster lookup → email OTP → "
        "certificate upload → disability-cell review."
    ),
)

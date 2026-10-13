from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Publicly known key, committed so `docker compose up` works out of the box.
# Never valid outside dev/test: the validator below refuses to start with it.
DEV_DOCUMENT_ENCRYPTION_KEY = "nowhJxoHbQ0yFohEPvYRFPMTrQBYzud50qKjhYH5Ohg="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"
    service_name: str = "rideassist"

    database_url: str = "postgresql+asyncpg://rideassist:rideassist@localhost:5432/rideassist"
    redis_url: str = "redis://localhost:6379/0"

    # --- auth -------------------------------------------------------------
    jwt_secret: str = "dev-only-jwt-secret-change-me-0123456789"  # noqa: S105
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    internal_api_key: str = "dev-only-internal-key"

    # --- verification -----------------------------------------------------
    otp_secret: str = "dev-only-otp-pepper-change-me"  # noqa: S105
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_sends_per_hour: int = 5
    document_encryption_key: str = DEV_DOCUMENT_ENCRYPTION_KEY
    max_document_bytes: int = 5 * 1024 * 1024
    storage_backend: str = "local"  # "local" | "s3"
    local_storage_dir: str = "/data/documents"
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "rideassist-documents"
    s3_region: str = "us-east-1"

    # --- notifications ----------------------------------------------------
    notification_service_url: str = "http://notification:8000"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "RideAssist <no-reply@rideassist.dev>"
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False

    # --- matching ---------------------------------------------------------
    offer_timeout_seconds: int = 15
    search_timeout_seconds: int = 120
    max_offer_attempts: int = 6
    matching_sweep_interval_seconds: float = 2.0
    driver_heartbeat_ttl_seconds: int = 60

    # --- routing ----------------------------------------------------------
    osrm_url: str | None = None
    avg_campus_speed_kmh: float = 18.0

    # Scales every per-IP limit; >1 for local dev/E2E where all traffic shares one IP.
    rate_limit_multiplier: float = 1.0

    cors_origins: list[str] = ["http://localhost:4200", "http://localhost:8100"]

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> "Settings":
        if self.environment not in {"dev", "test"}:
            weak = [
                name
                for name in ("jwt_secret", "otp_secret", "internal_api_key")
                if getattr(self, name).startswith("dev-only")
            ]
            if self.document_encryption_key == DEV_DOCUMENT_ENCRYPTION_KEY:
                weak.append("document_encryption_key")
            if weak:
                raise ValueError(f"Refusing to start with dev placeholder secrets: {weak}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

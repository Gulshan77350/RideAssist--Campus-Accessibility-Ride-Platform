"""Internal-only notification fan-out (email via SMTP, push and SMS adapters).

Not routed through the public gateway; callers authenticate with X-API-Key.
Push/SMS are logged and kept in a short Redis inbox until FCM/Twilio credentials are wired in,
so the whole flow can be demoed locally without third-party accounts.
"""

import json
import logging
import time
from email.message import EmailMessage

import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from redis.asyncio import Redis

from rideassist_common.app_factory import create_app
from rideassist_common.config import get_settings
from rideassist_common.deps import require_internal_api_key
from rideassist_common.observability import log_extra
from rideassist_common.redis import get_redis

log = logging.getLogger(__name__)
router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_api_key)]
)
INBOX_SIZE = 50


class EmailIn(BaseModel):
    to: EmailStr
    subject: str = Field(max_length=200)
    body: str = Field(max_length=10_000)


class PushIn(BaseModel):
    user_id: str
    title: str = Field(max_length=120)
    body: str = Field(max_length=500)
    data: dict = {}


class SmsIn(BaseModel):
    phone: str = Field(pattern=r"^\+?\d{10,15}$")
    body: str = Field(max_length=480)


@router.post("/email", status_code=status.HTTP_202_ACCEPTED)
async def send_email(body: EmailIn) -> dict:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = body.to
    msg["Subject"] = body.subject
    msg.set_content(body.body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_username,
            password=s.smtp_password,
            start_tls=s.smtp_starttls,
            timeout=10,
        )
    except (aiosmtplib.SMTPException, OSError) as exc:
        log.error("smtp send failed", extra=log_extra(to=body.to, error=str(exc)))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "SMTP delivery failed") from exc
    log.info("email sent", extra=log_extra(to=body.to, subject=body.subject))
    return {"status": "sent"}


@router.post("/push", status_code=status.HTTP_202_ACCEPTED)
async def push(body: PushIn, redis: Redis = Depends(get_redis)) -> dict:
    key = f"notifications:push:{body.user_id}"
    record = json.dumps({**body.model_dump(), "ts": time.time()})
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lpush(key, record)
        pipe.ltrim(key, 0, INBOX_SIZE - 1)
        pipe.publish(f"user:{body.user_id}:notifications", record)
        await pipe.execute()
    log.info("push queued", extra=log_extra(user_id=body.user_id, title=body.title))
    return {"status": "queued", "adapter": "redis-inbox"}


@router.get("/push/{user_id}")
async def push_inbox(user_id: str, redis: Redis = Depends(get_redis)) -> list[dict]:
    return [json.loads(r) for r in await redis.lrange(f"notifications:push:{user_id}", 0, -1)]


@router.post("/sms", status_code=status.HTTP_202_ACCEPTED)
async def sms(body: SmsIn) -> dict:
    log.info(
        "sms (stub adapter)", extra=log_extra(phone=body.phone[-4:].rjust(len(body.phone), "*"))
    )
    return {"status": "queued", "adapter": "log-stub"}


app = create_app("notification", [router], description="Internal email / push / SMS fan-out.")

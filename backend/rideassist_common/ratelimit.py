from redis.asyncio import Redis


async def hit(redis: Redis, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns (allowed, seconds_until_reset)."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    return count <= limit, max(int(ttl), 1)

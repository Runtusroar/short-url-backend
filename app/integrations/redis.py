from redis.asyncio import Redis

REDIS_CONNECT_TIMEOUT_SECONDS = 1.0
REDIS_READ_TIMEOUT_SECONDS = 1.0


def create_redis_client(url: str) -> Redis:
    return Redis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=REDIS_READ_TIMEOUT_SECONDS,
    )


async def close_redis_client(client: Redis) -> None:
    await client.aclose()

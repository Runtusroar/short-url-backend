from redis.asyncio import Redis


def create_redis_client(url: str) -> Redis:
    return Redis.from_url(url, encoding="utf-8", decode_responses=True)


async def close_redis_client(client: Redis) -> None:
    await client.aclose()

"""Production Redis client timeout contracts."""

import math

from app.integrations.redis import close_redis_client, create_redis_client


async def test_production_redis_client_has_finite_connect_and_read_timeouts():
    client = create_redis_client("redis://localhost:6379/0")
    try:
        connection_options = client.connection_pool.connection_kwargs
        connect_timeout = connection_options["socket_connect_timeout"]
        read_timeout = connection_options["socket_timeout"]

        assert math.isfinite(connect_timeout) and connect_timeout > 0
        assert math.isfinite(read_timeout) and read_timeout > 0
    finally:
        await close_redis_client(client)

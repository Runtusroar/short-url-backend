"""Parse access-log user agents in idempotent, short transactions."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import AsyncSessionLocal
from app.services.user_agent import parse_user_agent


UPDATE_LOG = text(
    """
    UPDATE access_logs
    SET ua_browser = :browser,
        ua_browser_version = :browser_version,
        ua_os = :os,
        ua_os_version = :os_version,
        ua_device_type = :device_type,
        ua_device_brand = :brand,
        ua_device_model = :model,
        ua_bot_name = :bot_name
    WHERE id = :id AND ua_raw IS NOT NULL AND ua_browser IS NULL
    """
)


async def backfill_user_agents(batch_size: int) -> int:
    processed = 0
    while True:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT id, ua_raw
                        FROM access_logs
                        WHERE ua_raw IS NOT NULL AND ua_browser IS NULL
                        ORDER BY id
                        LIMIT :batch_size
                        FOR UPDATE SKIP LOCKED
                        """
                    ),
                    {"batch_size": batch_size},
                )
            ).mappings().all()
            if not rows:
                return processed
            for row in rows:
                parsed = parse_user_agent(row["ua_raw"])
                await session.execute(
                    UPDATE_LOG,
                    {
                        "id": row["id"],
                        # A parser can legitimately identify no browser.  The
                        # sentinel records that this raw UA was processed.
                        "browser": parsed.browser or "Unknown",
                        "browser_version": parsed.browser_version,
                        "os": parsed.os,
                        "os_version": parsed.os_version,
                        "device_type": parsed.device_type,
                        "brand": parsed.brand,
                        "model": parsed.model,
                        "bot_name": parsed.bot_name,
                    },
                )
            await session.commit()
            processed += len(rows)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    return args


def main() -> int:
    args = _args()
    asyncio.run(backfill_user_agents(args.batch_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

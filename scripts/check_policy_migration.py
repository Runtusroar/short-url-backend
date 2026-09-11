"""Fail closed before the schema migration removes legacy access rules."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import text

from app.db import AsyncSessionLocal
from app.services.policy_migration import find_unconvertible_links


async def check_policy_migration() -> list[str]:
    async with AsyncSessionLocal() as session:
        links = (await session.execute(text("SELECT id, default_action FROM short_links ORDER BY id"))).mappings().all()
        pairs = []
        for link in links:
            rules = (
                await session.execute(
                    text(
                        """
                        SELECT id, action, priority, countries, ua_platforms, referer_pattern,
                               allow_proxy, allow_bot, is_active
                        FROM access_rules WHERE short_link_id = :link_id
                        ORDER BY priority, id
                        """
                    ),
                    {"link_id": link["id"]},
                )
            ).mappings().all()
            pairs.append((SimpleNamespace(**dict(link)), [SimpleNamespace(**dict(rule)) for rule in rules]))
    return [link_id for conversion in find_unconvertible_links(pairs) for link_id in conversion.affected_ids]


def main() -> int:
    affected_ids = asyncio.run(check_policy_migration())
    for link_id in affected_ids:
        print(link_id)
    return 1 if affected_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())

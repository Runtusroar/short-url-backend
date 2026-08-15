import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Domain


async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_domain.py <domain-name> [--default]")
        sys.exit(1)
    name = sys.argv[1].lower()
    is_default = "--default" in sys.argv

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Domain).where(Domain.name == name))
        if existing.scalar_one_or_none():
            print(f"Domain '{name}' already exists")
            return

        if is_default:
            await db.execute(Domain.__table__.update().values(is_default=False))

        domain = Domain(name=name, is_active=True, is_default=is_default)
        db.add(domain)
        await db.commit()
        print(f"Created domain '{name}' (default={is_default})")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.db.models import Domain


async def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_domain.py <domain-name>")
        sys.exit(1)
    name = sys.argv[1].lower()

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Domain).where(Domain.name == name))
        if existing.scalar_one_or_none():
            print(f"Domain '{name}' already exists")
            return

        domain = Domain(name=name, is_active=True)
        db.add(domain)
        await db.commit()
        print(f"Created domain '{name}'")


if __name__ == "__main__":
    asyncio.run(main())

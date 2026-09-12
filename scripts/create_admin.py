import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.core.security import get_password_hash
from app.db.models import User


async def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_admin.py <username> <password>")
        sys.exit(1)
    username, password = sys.argv[1], sys.argv[2]
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user:
            user.password_hash = get_password_hash(password)
            user.role = "admin"
            user.is_active = True
            print(f"Updated existing user '{username}' as admin")
        else:
            user = User(
                username=username,
                password_hash=get_password_hash(password),
                role="admin",
                is_active=True,
            )
            db.add(user)
            print(f"Created admin user '{username}'")
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())

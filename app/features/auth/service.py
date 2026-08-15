from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError, UnauthorizedError
from app.core.security import create_access_token, verify_password
from app.features.users.schemas import normalize_username
from app.models import User


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User:
    normalized_username = normalize_username(username)
    result = await db.execute(select(User).where(User.username == normalized_username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise PermissionDeniedError("用户已停用")
    return user


def issue_token(user: User) -> str:
    return create_access_token({"sub": str(user.id)})

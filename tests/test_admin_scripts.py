import sys

import pytest
from sqlalchemy import func, select

from app.db.models import Domain, User, UserDomainAccess, UserRole
from app.db.session import AsyncSessionLocal
from scripts import create_admin, create_domain


async def test_create_domain_normalizes_a_trailing_dot_before_persisting(monkeypatch):
    """CLI-created tenants must route exactly like API-created tenants."""
    monkeypatch.setattr(sys, "argv", ["create_domain.py", "Example.COM."])

    await create_domain.main()

    async with AsyncSessionLocal() as session:
        stored = await session.scalar(select(Domain).where(Domain.name == "example.com"))
    assert stored is not None


async def test_create_domain_reports_invalid_hostname_to_stderr_and_exits_nonzero(
    monkeypatch, capsys
):
    """Accepting URI-shaped input would create a tenant that request routing rejects."""
    monkeypatch.setattr(sys, "argv", ["create_domain.py", "https://example.com"])

    with pytest.raises(SystemExit) as exited:
        await create_domain.main()

    assert exited.value.code == 1
    assert "Invalid domain name" in capsys.readouterr().err


async def test_create_admin_promotion_removes_existing_subaccount_grants(monkeypatch):
    """Promoting a scoped user must not preserve obsolete authorization rows."""
    monkeypatch.setattr(sys, "argv", ["create_admin.py", "reader", "new-admin-password"])

    await create_admin.main()

    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == "reader"))
        assert user is not None
        grant_count = await session.scalar(
            select(func.count())
            .select_from(UserDomainAccess)
            .where(UserDomainAccess.user_id == user.id)
        )
    assert user.role == UserRole.ADMIN
    assert grant_count == 0

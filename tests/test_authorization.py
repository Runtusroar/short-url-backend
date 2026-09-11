import pytest
from sqlalchemy import select

from app.db.models import AccessLevel, Domain, User, UserDomainAccess
from app.exceptions import PermissionDeniedError
from app.services.authorization import (
    authorized_domain_ids_query,
    ensure_domain_access,
)


@pytest.mark.parametrize(
    ("role", "granted", "required", "allowed"),
    [
        ("admin", None, "manage", True),
        ("subaccount", "read", "read", True),
        ("subaccount", "read", "manage", False),
        ("subaccount", "manage", "read", True),
        ("subaccount", "manage", "manage", True),
        ("subaccount", None, "read", False),
    ],
)
async def test_domain_access_matrix(db, make_user, make_domain, role, granted, required, allowed):
    user = await make_user(role=role)
    domain = await make_domain()
    if granted is not None:
        db.add(UserDomainAccess(user_id=user.id, domain_id=domain.id, access_level=granted))
        await db.flush()

    if allowed:
        assert await ensure_domain_access(db, user, domain.id, AccessLevel(required)) == domain
    else:
        with pytest.raises(PermissionDeniedError):
            await ensure_domain_access(db, user, domain.id, AccessLevel(required))


async def test_authorized_domain_ids_query_limits_subaccounts_to_active_grants(db, domain_a, domain_b):
    reader = await db.scalar(select(User).where(User.username == "reader"))
    domain_b.is_active = False
    await db.flush()

    domain_ids = set((await db.scalars(authorized_domain_ids_query(reader))).all())

    assert domain_ids == {domain_a.id}


async def test_authorized_domain_ids_query_includes_all_active_domains_for_admin(db, domain_a, domain_b):
    admin = await db.scalar(select(User).where(User.username == "admin"))

    domain_ids = set((await db.scalars(authorized_domain_ids_query(admin))).all())

    assert {domain_a.id, domain_b.id}.issubset(domain_ids)

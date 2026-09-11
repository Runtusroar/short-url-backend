import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionDeniedError
from app.db.models import AccessLevel, Domain, User, UserDomainAccess
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


async def test_inactive_grant_is_excluded_and_rejected(db, make_user, make_domain):
    reader = await make_user(role="subaccount")
    domain = await make_domain(is_active=False)
    db.add(UserDomainAccess(user_id=reader.id, domain_id=domain.id, access_level="manage"))
    await db.flush()

    domain_ids = set((await db.scalars(authorized_domain_ids_query(reader))).all())

    assert domain.id not in domain_ids
    with pytest.raises(NotFoundError):
        await ensure_domain_access(db, reader, domain.id, AccessLevel.READ)


async def test_authorized_domain_ids_query_includes_all_active_domains_for_admin(db, domain_a, domain_b):
    admin = await db.scalar(select(User).where(User.username == "admin"))

    domain_ids = set((await db.scalars(authorized_domain_ids_query(admin))).all())

    assert {domain_a.id, domain_b.id}.issubset(domain_ids)

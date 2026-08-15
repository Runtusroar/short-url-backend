"""Real-database regression coverage for user-service conflict paths."""

import uuid

import pytest
from sqlalchemy import event, select, text

from app.core.database import AsyncSessionLocal, engine
from app.core.exceptions import ConflictError
from app.core.security import get_password_hash
from app.features.users.schemas import UserCreate, UserUpdate
from app.features.users.service import create_user, update_user
from app.models import User, UserDomain
from tests.conftest import _sync_engine


def _insert_competing_user(username: str) -> None:
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                VALUES (:id, :username, :password_hash, 'client', true, now(), now())
                """
            ),
            {
                "id": uuid.uuid4(),
                "username": username,
                "password_hash": get_password_hash("secret1"),
            },
        )


def _commit_competitor_after_username_precheck(username: str):
    committed = False

    def commit_competitor(conn, cursor, statement, parameters, context, executemany):
        nonlocal committed
        if committed or "WHERE users.username" not in statement:
            return
        _insert_competing_user(username)
        committed = True

    event.listen(engine.sync_engine, "after_cursor_execute", commit_competitor)

    def remove_listener() -> None:
        event.remove(engine.sync_engine, "after_cursor_execute", commit_competitor)

    return remove_listener


async def test_create_user_rolls_back_flush_uniqueness_conflict_from_other_session():
    username = "flush-race"
    remove_listener = _commit_competitor_after_username_precheck(username)
    actor = User(id=uuid.uuid4())
    try:
        async with AsyncSessionLocal() as db:
            with pytest.raises(ConflictError):
                await create_user(
                    db,
                    UserCreate(
                        username=username,
                        password="secret1",
                        role="client",
                        domain_ids=[],
                    ),
                    actor,
                )
            assert (
                await db.scalar(select(User.id).where(User.username == username))
            ) is not None
    finally:
        remove_listener()


async def test_update_user_rolls_back_autoflush_uniqueness_conflict_from_other_session():
    target_id = uuid.uuid4()
    updated_username = "autoflush-race"
    with _sync_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at)
                VALUES (:id, 'update-target', :password_hash, 'client', true, now(), now())
                """
            ),
            {"id": target_id, "password_hash": get_password_hash("secret1")},
        )
        domain_id = connection.scalar(
            text("SELECT id FROM domains WHERE name = 'test.local'")
        )
    remove_listener = _commit_competitor_after_username_precheck(updated_username)
    actor = User(id=uuid.uuid4())
    try:
        async with AsyncSessionLocal() as db:
            with pytest.raises(ConflictError):
                await update_user(
                    db,
                    target_id,
                    UserUpdate(username=updated_username, domain_ids=[domain_id]),
                    actor,
                )
            assert (
                await db.scalar(select(User.username).where(User.id == target_id))
            ) == "update-target"
            assert (
                await db.scalar(
                    select(UserDomain.id).where(UserDomain.user_id == target_id)
                )
            ) is None
    finally:
        remove_listener()

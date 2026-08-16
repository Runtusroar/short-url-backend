import pytest

from tests.migrations.support import disposable_migration_database


@pytest.fixture
def migration_database_url() -> str:
    with disposable_migration_database() as database_url:
        yield database_url

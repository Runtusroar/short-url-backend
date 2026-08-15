from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import AccessLog, ShortLink


EXPECTED = {
    "short_links": {
        "idx_short_links_domain": ("domain_id",),
    },
    "access_logs": {
        "idx_access_logs_domain": ("domain_id",),
        "idx_access_logs_short_link": ("short_link_id",),
        "idx_access_logs_plus8": ("short_link_id", "accessed_at_plus8"),
        "idx_access_logs_dedup": ("short_link_id", "ip", "dedup_bucket"),
    },
}


def index_map(table):
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }


def test_orm_declares_repaired_baseline_indexes():
    assert index_map(ShortLink.__table__) == EXPECTED["short_links"]
    assert index_map(AccessLog.__table__) == EXPECTED["access_logs"]


def test_target_url_foreign_key_remains_set_null():
    foreign_keys = list(AccessLog.__table__.c.target_url_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].ondelete == "SET NULL"


def test_repair_revision_extends_current_head():
    revision_path = (
        Path(__file__).parents[2]
        / "alembic/versions/c4b7e2a19f03_restore_baseline_indexes.py"
    )
    spec = spec_from_file_location("restore_baseline_indexes", revision_path)
    assert spec is not None
    assert spec.loader is not None
    revision = module_from_spec(spec)
    spec.loader.exec_module(revision)

    assert revision.revision == "c4b7e2a19f03"
    assert revision.down_revision == "a9e56b03bf5f"

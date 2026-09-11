from datetime import datetime, timezone

from sqlalchemy.orm import declarative_base


Base = declarative_base()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, IPvAnyAddress, field_validator
from pydantic_core import PydanticCustomError


def _contract_error(message: str) -> PydanticCustomError:
    return PydanticCustomError("unprocessable_entity", message)


class IpBlacklistCreate(BaseModel):
    ip: IPvAnyAddress
    reason: str
    expires_at: datetime | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str:
        if not isinstance(value, str) or not (normalized := value.strip()):
            raise _contract_error("reason must not be blank")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise _contract_error("expires_at must include a timezone")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise _contract_error("expires_at must be in the future")
        return normalized


class IpBlacklistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ip: IPvAnyAddress
    reason: str
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime

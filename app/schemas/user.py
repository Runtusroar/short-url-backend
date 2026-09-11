from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import AccessLevel, UserRole


class DomainGrantWrite(BaseModel):
    domain_id: UUID
    access_level: AccessLevel


class DomainGrantResponse(DomainGrantWrite):
    pass


class UserWrite(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None
    domain_access: list[DomainGrantWrite] | None = None

    @field_validator("domain_access")
    @classmethod
    def domain_grants_must_be_unique(
        cls, grants: list[DomainGrantWrite] | None
    ) -> list[DomainGrantWrite] | None:
        if grants is not None and len({grant.domain_id for grant in grants}) != len(grants):
            raise ValueError("域名权限不能重复")
        return grants


class UserCreate(UserWrite):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = UserRole.SUBACCOUNT
    domain_access: list[DomainGrantWrite] = Field(default_factory=list)


class UserUpdate(UserWrite):
    pass


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime
    domain_access: list[DomainGrantResponse] = Field(default_factory=list)

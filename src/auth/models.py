from enum import Enum

from pydantic import BaseModel, field_validator

from src.identifiers import validate_identifier


class Role(str, Enum):
    EMPLOYEE = "employee"
    ADMIN = "admin"


class UserContext(BaseModel):
    user_id: str
    tenant_id: str
    role: Role

    @field_validator("tenant_id", mode="before")
    @classmethod
    def valid_tenant_id(cls, value):
        return validate_identifier(value)

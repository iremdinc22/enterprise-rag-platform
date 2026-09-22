from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    EMPLOYEE = "employee"
    ADMIN = "admin"


class UserContext(BaseModel):
    user_id: str
    tenant_id: str
    role: Role
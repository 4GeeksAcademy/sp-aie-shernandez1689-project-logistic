from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RoleEnum(str, Enum):
    admin = "admin"
    manager = "manager"
    user = "user"


class UserRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    email: EmailStr
    hashed_password: str
    is_active: bool = True
    role: RoleEnum = RoleEnum.user
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProfileRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: RoleEnum = RoleEnum.user
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

    @field_validator("role")
    @classmethod
    def enforce_default_role_for_signup(cls, value: RoleEnum) -> RoleEnum:
        if value != RoleEnum.user:
            raise ValueError("New users created via POST /users must use role 'user'")
        return value


class UserUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8)
    role: Optional[RoleEnum] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    role: RoleEnum
    created_at: str
    profile: Optional[ProfileRecord] = None


class AuthUser(BaseModel):
    id: str
    email: EmailStr
    role: RoleEnum
    is_active: bool


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class ProfileResponse(BaseModel):
    id: str
    user_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthMeResponse(BaseModel):
    email: EmailStr
    role: RoleEnum
    profile: Optional[ProfileResponse] = None

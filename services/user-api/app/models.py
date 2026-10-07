from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator


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


class ApplicationSubmissionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    company_name: str
    contact_person: str
    corporate_email: EmailStr
    phone: str
    company_website: Optional[str] = None
    operating_country: str
    product_type: str
    monthly_volume: str
    services_interest: list[str]
    current_3pl: str
    comments: Optional[str] = None
    privacy_policy_accepted: bool
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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


class ApplicationSubmissionRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    contact_person: str = Field(min_length=3, max_length=120)
    corporate_email: EmailStr
    phone: str = Field(min_length=7, max_length=40)
    company_website: Optional[HttpUrl] = None
    operating_country: str = Field(min_length=2, max_length=40)
    product_type: str = Field(min_length=2, max_length=40)
    monthly_volume: str = Field(min_length=1, max_length=20)
    services_interest: list[str] = Field(min_length=1)
    current_3pl: str = Field(min_length=2, max_length=20)
    comments: Optional[str] = Field(default=None, max_length=500)
    privacy_policy_accepted: bool

    @field_validator("contact_person")
    @classmethod
    def validate_contact_person(cls, value: str) -> str:
        words = [word for word in value.strip().split() if word]
        if len(words) < 2:
            raise ValueError("Contact person must include first and last name")
        return value.strip()

    @field_validator("services_interest")
    @classmethod
    def validate_services_interest(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("At least one service is required")
        return cleaned

    @field_validator("privacy_policy_accepted")
    @classmethod
    def validate_privacy_policy(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Privacy policy acceptance is required")
        return value


class ApplicationSubmissionResponse(BaseModel):
    id: str
    status: str
    message: str

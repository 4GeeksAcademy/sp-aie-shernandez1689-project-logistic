from typing import Any, Optional

from tinydb import Query

from app.db import profiles_table, users_table
from app.models import ProfileRecord, RoleEnum, UserRecord
from app.security import hash_password


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def create_user(
    email: str,
    password: str,
    role: RoleEnum = RoleEnum.user,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
) -> dict[str, Any]:
    existing = get_user_by_email(email)
    if existing:
        raise UserAlreadyExistsError("A user with this email already exists")

    user = UserRecord(email=email, hashed_password=hash_password(password), role=role)
    users_table.insert(user.model_dump())

    if any([name, phone, address]):
        profile = ProfileRecord(user_id=user.id, name=name, phone=phone, address=address)
        profiles_table.insert(profile.model_dump())

    return get_user_by_id_or_raise(user.id)


def list_users() -> list[dict[str, Any]]:
    return [attach_profile(user) for user in users_table.all()]


def get_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    user = users_table.get(user_query.id == user_id)
    if not user:
        return None
    return attach_profile(user)


def get_user_by_id_or_raise(user_id: str) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        raise UserNotFoundError("User not found")
    return user


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    user = users_table.get(user_query.email == email)
    if not user:
        return None
    return attach_profile(user)


def update_user(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if "password" in updates:
        updates["hashed_password"] = hash_password(updates.pop("password"))

    allowed_fields = {"email", "hashed_password", "role", "is_active"}
    payload = {key: value for key, value in updates.items() if key in allowed_fields and value is not None}

    if not payload:
        return get_user_by_id_or_raise(user_id)

    user_query = Query()
    target = users_table.get(user_query.id == user_id)
    if not target:
        raise UserNotFoundError("User not found")

    if "email" in payload:
        existing = users_table.get((user_query.email == payload["email"]) & (user_query.id != user_id))
        if existing:
            raise UserAlreadyExistsError("A user with this email already exists")

    users_table.update(payload, user_query.id == user_id)
    return get_user_by_id_or_raise(user_id)


def delete_user(user_id: str) -> None:
    user_query = Query()
    removed = users_table.remove(user_query.id == user_id)
    if not removed:
        raise UserNotFoundError("User not found")
    profiles_table.remove(user_query.user_id == user_id)


def attach_profile(user: dict[str, Any]) -> dict[str, Any]:
    profile_query = Query()
    profile = profiles_table.get(profile_query.user_id == user["id"])
    result = {key: value for key, value in user.items() if key != "hashed_password"}
    result["profile"] = profile
    return result


def get_user_auth_record(user_id: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    return users_table.get(user_query.id == user_id)


def get_user_auth_record_by_email(email: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    return users_table.get(user_query.email == email)

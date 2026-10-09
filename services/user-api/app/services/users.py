import hashlib
import logging
from threading import RLock
from typing import Any, Optional

from tinydb import Query

from app.db import profiles_table, users_table
from app.models import ProfileRecord, RoleEnum, UserRecord
from app.security import (
    create_password_reset_token,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.services.email import EmailDeliveryError, send_password_reset_email


credentials_lock = RLock()
logger = logging.getLogger(__name__)


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class InvalidPasswordResetError(Exception):
    pass


class IncorrectPasswordError(Exception):
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

    try:
        profile = ProfileRecord(user_id=user.id, name=name, phone=phone, address=address)
        profiles_table.insert(profile.model_dump())
    except Exception:
        users_table.remove(Query().id == user.id)
        raise

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
    with credentials_lock:
        return _update_user(user_id, updates)


def _update_user(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
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

    def apply_updates(record: dict[str, Any]) -> None:
        record.update(payload)
        if "hashed_password" in payload or "email" in payload or payload.get("is_active") is False:
            record.pop("password_reset_token_hash", None)

    users_table.update(apply_updates, user_query.id == user_id)
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
    result = {
        key: value for key, value in user.items()
        if key not in {"hashed_password", "password_reset_token_hash"}
    }
    result["profile"] = profile
    return result


def get_user_auth_record(user_id: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    return users_table.get(user_query.id == user_id)


def get_user_auth_record_by_email(email: str) -> Optional[dict[str, Any]]:
    user_query = Query()
    return users_table.get(user_query.email == email)


def request_password_reset(email: str) -> None:
    with credentials_lock:
        user = get_user_auth_record_by_email(email)
        if not user or not user.get("is_active", True):
            return
        token, _ = create_password_reset_token(user["id"])
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        users_table.update({"password_reset_token_hash": token_hash}, Query().id == user["id"])

    try:
        send_password_reset_email(user["email"], token)
    except EmailDeliveryError:
        with credentials_lock:
            users_table.update(
                lambda record: record.pop("password_reset_token_hash", None),
                (Query().id == user["id"]) & (Query().password_reset_token_hash == token_hash),
            )
        logger.warning("Password reset email delivery failed")


def reset_password(token: str, new_password: str) -> None:
    try:
        payload = decode_password_reset_token(token)
    except ValueError as exc:
        raise InvalidPasswordResetError("Invalid or expired reset token") from exc

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with credentials_lock:
        user = get_user_auth_record(payload["sub"])
        if not user or not user.get("is_active", True) or user.get("password_reset_token_hash") != token_hash:
            raise InvalidPasswordResetError("Invalid or expired reset token")
        update_user(user["id"], {"password": new_password})


def change_password(user_id: str, current_password: str, new_password: str) -> None:
    with credentials_lock:
        user = get_user_auth_record(user_id)
        if not user or not verify_password(current_password, user["hashed_password"]):
            raise IncorrectPasswordError("Current password is incorrect")
        update_user(user_id, {"password": new_password})

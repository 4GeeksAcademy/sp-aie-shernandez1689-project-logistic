from typing import Any, Optional

from tinydb import Query

from app.db import profiles_table
from app.models import ProfileRecord


class ProfileNotFoundError(Exception):
    pass


def list_profiles() -> list[dict[str, Any]]:
    return profiles_table.all()


def get_profile_by_id(profile_id: str) -> Optional[dict[str, Any]]:
    profile_query = Query()
    return profiles_table.get(profile_query.id == profile_id)


def get_profile_by_id_or_raise(profile_id: str) -> dict[str, Any]:
    profile = get_profile_by_id(profile_id)
    if not profile:
        raise ProfileNotFoundError("Profile not found")
    return profile


def get_profile_by_user_id(user_id: str) -> Optional[dict[str, Any]]:
    profile_query = Query()
    return profiles_table.get(profile_query.user_id == user_id)


def get_profile_by_user_id_or_raise(user_id: str) -> dict[str, Any]:
    profile = get_profile_by_user_id(user_id)
    if not profile:
        raise ProfileNotFoundError("Profile not found")
    return profile


def upsert_profile_for_user(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {"name", "phone", "address"}
    payload = {key: value for key, value in updates.items() if key in allowed_fields}

    profile_query = Query()
    existing = profiles_table.get(profile_query.user_id == user_id)

    if not existing:
        profile = ProfileRecord(user_id=user_id, **payload)
        profiles_table.insert(profile.model_dump())
        return profile.model_dump()

    profiles_table.update(payload, profile_query.user_id == user_id)
    return get_profile_by_user_id_or_raise(user_id)


def update_existing_profile_for_user(user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {"name", "phone", "address"}
    payload = {key: value for key, value in updates.items() if key in allowed_fields}

    profile_query = Query()
    existing = profiles_table.get(profile_query.user_id == user_id)
    if not existing:
        raise ProfileNotFoundError("Profile not found")

    profiles_table.update(payload, profile_query.user_id == user_id)
    return get_profile_by_user_id_or_raise(user_id)


def delete_profile_by_user_id(user_id: str) -> None:
    profile_query = Query()
    removed = profiles_table.remove(profile_query.user_id == user_id)
    if not removed:
        raise ProfileNotFoundError("Profile not found")
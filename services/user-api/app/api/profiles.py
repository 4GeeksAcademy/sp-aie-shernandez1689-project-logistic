from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import AuthUser, ProfileResponse, ProfileUpdateRequest, RoleEnum
from app.services.profiles import (
    ProfileNotFoundError,
    delete_profile_by_user_id,
    get_profile_by_id_or_raise,
    get_profile_by_user_id_or_raise,
    list_profiles,
    update_existing_profile_for_user,
    upsert_profile_for_user,
)


router = APIRouter(prefix="/profiles", tags=["profiles"])


def _ensure_admin(user: AuthUser) -> None:
    if user.role != RoleEnum.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


@router.get("", response_model=list[ProfileResponse])
def get_all_profiles(current_user: AuthUser = Depends(get_current_user)) -> list[ProfileResponse]:
    _ensure_admin(current_user)
    return [ProfileResponse.model_validate(profile) for profile in list_profiles()]


@router.get("/me", response_model=ProfileResponse)
def get_my_profile(current_user: AuthUser = Depends(get_current_user)) -> ProfileResponse:
    try:
        profile = get_profile_by_user_id_or_raise(current_user.id)
        return ProfileResponse.model_validate(profile)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/me", response_model=ProfileResponse)
def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileResponse:
    updated = upsert_profile_for_user(current_user.id, payload.model_dump(exclude_none=True))
    return ProfileResponse.model_validate(updated)


@router.get("/user/{user_id}", response_model=ProfileResponse)
def get_profile_by_user(
    user_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileResponse:
    is_self = current_user.id == user_id
    is_admin = current_user.role == RoleEnum.admin
    if not is_self and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    try:
        profile = get_profile_by_user_id_or_raise(user_id)
        return ProfileResponse.model_validate(profile)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile_by_id(
    profile_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileResponse:
    _ensure_admin(current_user)
    try:
        profile = get_profile_by_id_or_raise(profile_id)
        return ProfileResponse.model_validate(profile)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/user/{user_id}", response_model=ProfileResponse)
def update_profile_by_user(
    user_id: str,
    payload: ProfileUpdateRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> ProfileResponse:
    _ensure_admin(current_user)
    try:
        updated = update_existing_profile_for_user(user_id, payload.model_dump(exclude_none=True))
        return ProfileResponse.model_validate(updated)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_profile_by_user(
    user_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> None:
    _ensure_admin(current_user)
    try:
        delete_profile_by_user_id(user_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import AuthUser, RoleEnum, UserCreateRequest, UserResponse, UserUpdateRequest
from app.services.users import (
    UserAlreadyExistsError,
    UserNotFoundError,
    create_user,
    delete_user,
    get_user_by_id_or_raise,
    list_users,
    update_user,
)


router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreateRequest) -> UserResponse:
    try:
        user = create_user(
            email=payload.email,
            password=payload.password,
            role=RoleEnum.user,
            name=payload.name,
            phone=payload.phone,
            address=payload.address,
        )
        return UserResponse.model_validate(user)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[UserResponse])
def get_users(current_user: AuthUser = Depends(get_current_user)) -> list[UserResponse]:
    if current_user.role != RoleEnum.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can list all users",
        )

    users = list_users()
    return [UserResponse.model_validate(user) for user in users]


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: str, current_user: AuthUser = Depends(get_current_user)) -> UserResponse:
    is_self = current_user.id == user_id
    is_admin = current_user.role == RoleEnum.admin
    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the same user or an admin can view this account",
        )

    try:
        user = get_user_by_id_or_raise(user_id)
        return UserResponse.model_validate(user)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{user_id}", response_model=UserResponse)
def update_user_credentials(
    user_id: str,
    payload: UserUpdateRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> UserResponse:
    is_self = current_user.id == user_id
    is_admin = current_user.role == RoleEnum.admin

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the same user or an admin can update this account",
        )

    if payload.role is not None and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can change role",
        )

    try:
        updated = update_user(user_id, payload.model_dump(exclude_none=True))
        return UserResponse.model_validate(updated)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user(user_id: str, current_user: AuthUser = Depends(get_current_user)) -> None:
    is_self = current_user.id == user_id
    is_admin = current_user.role == RoleEnum.admin

    if not is_self and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the same user or an admin can delete this account",
        )

    try:
        delete_user(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

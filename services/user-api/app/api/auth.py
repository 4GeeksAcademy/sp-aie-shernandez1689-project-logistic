from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import (
    AuthMeResponse,
    AuthUser,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ProfileResponse,
    ResetPasswordRequest,
    TokenResponse,
)
from app.security import create_access_token, verify_password
from app.services.profiles import get_profile_by_user_id
from app.services.users import (
    IncorrectPasswordError,
    InvalidPasswordResetError,
    change_password,
    get_user_auth_record_by_email,
    request_password_reset,
    reset_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = get_user_auth_record_by_email(payload.email)
    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    token = create_access_token(subject=user["id"], email=user["email"], role=user["role"])
    return TokenResponse(access_token=token)


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    background_tasks.add_task(request_password_reset, payload.email)
    return {"message": "If the account exists, a password reset email will be sent."}


@router.post("/reset-password")
def reset_password_route(payload: ResetPasswordRequest) -> dict[str, str]:
    try:
        reset_password(payload.token, payload.new_password)
    except InvalidPasswordResetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"message": "Password reset successfully."}


@router.post("/change-password")
def change_password_route(
    payload: ChangePasswordRequest,
    current_user: AuthUser = Depends(get_current_user),
) -> dict[str, str]:
    try:
        change_password(current_user.id, payload.current_password, payload.new_password)
    except IncorrectPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"message": "Password changed successfully."}


@router.get("/me", response_model=AuthMeResponse)
def auth_me(current_user: AuthUser = Depends(get_current_user)) -> AuthMeResponse:
    profile = get_profile_by_user_id(current_user.id)
    profile_data = ProfileResponse.model_validate(profile) if profile else None
    return AuthMeResponse(email=current_user.email, role=current_user.role, profile=profile_data)
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import AuthMeResponse, AuthUser, LoginRequest, ProfileResponse, TokenResponse
from app.security import create_access_token, verify_password
from app.services.profiles import get_profile_by_user_id
from app.services.users import get_user_auth_record_by_email


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = get_user_auth_record_by_email(payload.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    if not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(subject=user["id"], email=user["email"], role=user["role"])
    return TokenResponse(access_token=token)


@router.get("/me", response_model=AuthMeResponse)
def auth_me(current_user: AuthUser = Depends(get_current_user)) -> AuthMeResponse:
    profile = get_profile_by_user_id(current_user.id)
    profile_data = ProfileResponse.model_validate(profile) if profile else None
    return AuthMeResponse(email=current_user.email, role=current_user.role, profile=profile_data)
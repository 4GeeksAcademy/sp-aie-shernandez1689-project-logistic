from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import AuthUser
from app.security import decode_access_token
from app.services.users import get_user_auth_record


bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthUser:
    token = credentials.credentials.strip() if credentials else ""
    if not token:
        raise _unauthorized("Missing Bearer token")

    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise _unauthorized(str(exc)) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("Invalid token payload")

    user_record = get_user_auth_record(user_id)
    if not user_record:
        raise _unauthorized("Unknown token")

    if not user_record.get("is_active", True):
        raise _unauthorized("Inactive user")

    return AuthUser(
        id=user_record["id"],
        email=user_record["email"],
        role=user_record["role"],
        is_active=user_record["is_active"],
    )
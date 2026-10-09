"""JWT authentication for the Supplier Directory API using tokens issued by user-api."""

from __future__ import annotations

import os
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel
from tinydb import Query, TinyDB


API_DIR = Path(__file__).resolve().parent
USER_API_DIR = API_DIR.parent / "user-api"

load_dotenv(API_DIR / ".env")
# Falls back to user-api's .env so both services share the same signing key.
load_dotenv(USER_API_DIR / ".env")

JWT_ALGORITHM = "HS256"
USER_DB_PATH = Path(os.getenv("USER_DB_PATH", USER_API_DIR / "data" / "db.json"))


class CurrentUser(BaseModel):
    id: str
    email: str
    role: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _get_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("Missing required environment variable: JWT_SECRET_KEY")
    return secret


def _find_user(user_id: str) -> dict | None:
    if not USER_DB_PATH.exists():
        return None
    db = TinyDB(USER_DB_PATH, access_mode="r")
    try:
        return db.table("users").get(Query().id == user_id)
    finally:
        db.close()


def get_current_user(authorization: str = Header(default="")) -> CurrentUser:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Missing Bearer token")

    try:
        payload = jwt.decode(
            token.strip(),
            _get_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid token") from exc

    user = _find_user(payload["sub"])
    if not user:
        raise _unauthorized("Unknown token")
    if not user.get("is_active", True):
        raise _unauthorized("Inactive user")

    return CurrentUser(id=user["id"], email=user["email"], role=user["role"])


def require_roles(*roles: str):
    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    return dependency

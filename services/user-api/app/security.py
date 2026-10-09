import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import jwt
from dotenv import load_dotenv
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from passlib.context import CryptContext


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


JWT_SECRET = _required_env("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(_required_env("ACCESS_TOKEN_EXPIRE_MINUTES"))
PASSWORD_RESET_EXPIRE_MINUTES = 30
PASSWORD_RESET_SECRET = hmac.new(
    JWT_SECRET.encode(), b"password-reset", hashlib.sha256
).hexdigest()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return pwd_context.verify(password, stored_hash)


def create_access_token(subject: str, email: str, role: str, expires_minutes: int | None = None) -> str:
    ttl_minutes = expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    payload = {
        "sub": subject,
        "email": email,
        "role": role,
        "exp": expires_at,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except ExpiredSignatureError as exc:
        raise ValueError("Token expired") from exc
    except InvalidTokenError as exc:
        raise ValueError("Invalid token") from exc


def create_password_reset_token(subject: str) -> tuple[str, str]:
    token_id = str(uuid4())
    payload = {
        "sub": subject,
        "jti": token_id,
        "purpose": "password-reset",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, PASSWORD_RESET_SECRET, algorithm=JWT_ALGORITHM), token_id


def decode_password_reset_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            PASSWORD_RESET_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "jti", "purpose"]},
        )
        if payload["purpose"] != "password-reset" or not isinstance(payload["jti"], str):
            raise ValueError("Invalid reset token")
        return payload
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired reset token") from exc

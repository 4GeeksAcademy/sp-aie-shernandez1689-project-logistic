from datetime import datetime, timedelta, timezone

import jwt

from app.security import JWT_ALGORITHM, PASSWORD_RESET_SECRET, decode_password_reset_token, verify_password
from app.services import users


def request_reset_token(client, user, monkeypatch):
    delivered = []
    monkeypatch.setattr(users, "send_password_reset_email", lambda email, token: delivered.append(token))
    response = client.post("/auth/forgot-password", json={"email": user["email"]})
    assert response.status_code == 200
    return delivered[0]


def test_reset_password_updates_password_and_consumes_token(client, user, monkeypatch):
    token = request_reset_token(client, user, monkeypatch)

    response = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "new-password"},
    )

    assert response.status_code == 200
    assert verify_password("new-password", users.get_user_auth_record(user["id"])["hashed_password"])
    assert client.post(
        "/auth/reset-password", json={"token": token, "new_password": "other-password"}
    ).status_code == 400


def test_reset_password_rejects_expired_token(client, user, monkeypatch):
    token = request_reset_token(client, user, monkeypatch)
    payload = decode_password_reset_token(token)
    payload["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired_token = jwt.encode(payload, PASSWORD_RESET_SECRET, algorithm=JWT_ALGORITHM)

    response = client.post(
        "/auth/reset-password",
        json={"token": expired_token, "new_password": "new-password"},
    )

    assert response.status_code == 400


def test_reset_password_rejects_invalid_token_and_password_limits(client):
    invalid_token = client.post(
        "/auth/reset-password",
        json={"token": "not-a-token", "new_password": "new-password"},
    )
    short_password = client.post(
        "/auth/reset-password",
        json={"token": "not-a-token", "new_password": "short"},
    )

    assert invalid_token.status_code == 400
    assert short_password.status_code == 422
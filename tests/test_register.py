import pytest

from app.db import profiles_table
from app.services import users


def test_register_creates_user_and_profile_without_exposing_password(client):
    response = client.post(
        "/users",
        json={"email": "new@example.com", "password": "valid-password", "name": "New User"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == "new@example.com"
    assert response.json()["role"] == "user"
    assert response.json()["profile"]["name"] == "New User"
    assert "hashed_password" not in response.json()


def test_register_rejects_duplicate_email(client, make_user):
    make_user()

    response = client.post(
        "/users",
        json={"email": "owner@example.com", "password": "valid-password"},
    )

    assert response.status_code == 409


def test_register_rejects_invalid_email_short_password_and_privileged_role(client):
    invalid_payloads = [
        {"email": "not-an-email", "password": "valid-password"},
        {"email": "new@example.com", "password": "short"},
        {"email": "new@example.com", "password": "valid-password", "role": "admin"},
    ]

    for payload in invalid_payloads:
        assert client.post("/users", json=payload).status_code == 422


def test_register_rolls_back_user_when_profile_creation_fails(client, monkeypatch):
    def fail_profile_insert(profile):
        raise RuntimeError("profile storage unavailable")

    monkeypatch.setattr(profiles_table, "insert", fail_profile_insert)

    with pytest.raises(RuntimeError, match="profile storage unavailable"):
        client.post(
            "/users",
            json={"email": "rollback@example.com", "password": "valid-password"},
        )

    assert users.get_user_auth_record_by_email("rollback@example.com") is None
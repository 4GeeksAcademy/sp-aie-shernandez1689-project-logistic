from app.db import profiles_table
from app.security import create_access_token, create_password_reset_token
from app.services import users


def test_token_authenticates_user_and_returns_profile(client, user, auth_headers):
    response = client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == user["email"]
    assert response.json()["profile"]["name"] == "Owner Example"


def test_token_without_profile_returns_null_profile(client, user):
    profiles_table.truncate()
    token = create_access_token(user["id"], user["email"], user["role"])

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["profile"] is None


def test_token_endpoint_rejects_missing_and_expired_tokens(client, user):
    expired = create_access_token(user["id"], user["email"], user["role"], expires_minutes=-1)
    missing = client.get("/auth/me")
    invalid = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_token_is_rejected_if_user_is_deactivated_after_login(client, user, auth_headers):
    users.update_user(user["id"], {"is_active": False})

    response = client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 401


def test_password_reset_token_cannot_authenticate(client, user):
    reset_token, _ = create_password_reset_token(user["id"])

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {reset_token}"})

    assert response.status_code == 401
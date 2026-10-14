from app.security import verify_password
from app.services import users


def test_change_password_updates_password(client, user, auth_headers):
    response = client.post(
        "/auth/change-password",
        json={"current_password": "old-password", "new_password": "new-password"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert verify_password("new-password", users.get_user_auth_record(user["id"])["hashed_password"])


def test_change_password_accepts_minimum_valid_new_password(client, user, auth_headers):
    response = client.post(
        "/auth/change-password",
        json={"current_password": "old-password", "new_password": "12345678"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert verify_password("12345678", users.get_user_auth_record(user["id"])["hashed_password"])


def test_change_password_rejects_incorrect_current_password(client, auth_headers):
    response = client.post(
        "/auth/change-password",
        json={"current_password": "incorrect", "new_password": "new-password"},
        headers=auth_headers,
    )

    assert response.status_code == 400


def test_change_password_rejects_missing_token_and_short_password(client, auth_headers):
    payload = {"current_password": "old-password", "new_password": "new-password"}
    unauthenticated = client.post("/auth/change-password", json=payload)
    short_password = client.post(
        "/auth/change-password",
        json={"current_password": "old-password", "new_password": "short"},
        headers=auth_headers,
    )

    assert unauthenticated.status_code == 401
    assert short_password.status_code == 422

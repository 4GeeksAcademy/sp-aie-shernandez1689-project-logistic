from app.security import decode_access_token
from app.services import users


def test_login_returns_access_token_for_valid_credentials(client, user):
    response = client.post(
        "/auth/login",
        json={"email": user["email"], "password": "old-password"},
    )

    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type"}
    assert response.json()["token_type"] == "bearer"
    claims = decode_access_token(response.json()["access_token"])
    assert claims["sub"] == user["id"]
    assert set(claims) == {"sub", "role", "exp"}


def test_login_rejects_inactive_account(client, user):
    users.update_user(user["id"], {"is_active": False})

    response = client.post(
        "/auth/login",
        json={"email": user["email"], "password": "old-password"},
    )

    assert response.status_code == 403


def test_login_rejects_bad_credentials_and_malformed_input(client, user):
    wrong_password = client.post(
        "/auth/login",
        json={"email": user["email"], "password": "wrong-password"},
    )
    unknown_email = client.post(
        "/auth/login",
        json={"email": "missing@example.com", "password": "wrong-password"},
    )
    malformed = client.post("/auth/login", json={"email": "invalid", "password": "short"})

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == wrong_password.status_code
    assert unknown_email.json() == wrong_password.json()
    assert malformed.status_code == 422
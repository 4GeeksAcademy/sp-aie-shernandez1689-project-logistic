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
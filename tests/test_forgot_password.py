from app.services import users


def test_forgot_password_schedules_email_for_existing_account(client, user, monkeypatch):
    delivered = []
    monkeypatch.setattr(users, "send_password_reset_email", lambda email, token: delivered.append((email, token)))

    response = client.post("/auth/forgot-password", json={"email": user["email"]})

    assert response.status_code == 200
    assert response.json() == {
        "message": "If the account exists, a password reset email will be sent."
    }
    assert delivered[0][0] == user["email"]
    assert delivered[0][1]


def test_forgot_password_does_not_disclose_unknown_account(client, user, monkeypatch):
    delivered = []
    monkeypatch.setattr(users, "send_password_reset_email", lambda email, token: delivered.append(email))

    known = client.post("/auth/forgot-password", json={"email": user["email"]})
    unknown = client.post("/auth/forgot-password", json={"email": "missing@example.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert delivered == [user["email"]]


def test_forgot_password_rejects_malformed_email(client):
    response = client.post("/auth/forgot-password", json={"email": "invalid"})

    assert response.status_code == 422
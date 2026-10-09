import hashlib
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import jwt
from fastapi.testclient import TestClient
from tinydb import TinyDB
from tinydb.storages import MemoryStorage


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET_KEY"] = "password-tests-only-secret"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
test_db = TinyDB(storage=MemoryStorage)
with patch("tinydb.TinyDB", return_value=test_db):
    from app.main import app

from app.security import (
    JWT_ALGORITHM,
    PASSWORD_RESET_SECRET,
    create_access_token,
    decode_password_reset_token,
    verify_password,
)
from app.services import email, users


class PasswordEndpointsTests(unittest.TestCase):
    def setUp(self):
        for table in test_db.tables():
            test_db.table(table).truncate()
        self.user = users.create_user("owner@example.com", "old-password")
        self.client = TestClient(app)
        self.mail = patch("app.services.users.send_password_reset_email").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.client.close)

    def request_token(self):
        response = self.client.post("/auth/forgot-password", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 200)
        return self.mail.call_args.args[1]

    def reset(self, token, password="new-password"):
        return self.client.post("/auth/reset-password", json={"token": token, "new_password": password})

    def authorization(self):
        token = create_access_token(self.user["id"], self.user["email"], "user")
        return {"Authorization": f"Bearer {token}"}

    def test_forgot_always_returns_same_response(self):
        known = self.client.post("/auth/forgot-password", json={"email": self.user["email"]})
        unknown = self.client.post("/auth/forgot-password", json={"email": "unknown@example.com"})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(known.json(), unknown.json())
        self.mail.assert_called_once()

    def test_reset_hashes_password_and_consumes_token(self):
        token = self.request_token()
        stored = users.get_user_auth_record(self.user["id"])
        self.assertEqual(stored["password_reset_token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn("password_reset_token_hash", users.get_user_by_id(self.user["id"]))
        self.assertEqual(self.reset(token).status_code, 200)
        self.assertEqual(self.reset(token).status_code, 400)
        stored = users.get_user_auth_record(self.user["id"])
        self.assertNotIn("password_reset_token_hash", stored)
        self.assertNotEqual(stored["hashed_password"], "new-password")
        self.assertTrue(verify_password("new-password", stored["hashed_password"]))
        for password, expected in [("old-password", 401), ("new-password", 200)]:
            response = self.client.post("/auth/login", json={"email": self.user["email"], "password": password})
            self.assertEqual(response.status_code, expected)

    def test_reset_rejects_invalid_expired_and_session_tokens(self):
        token = self.request_token()
        payload = decode_password_reset_token(token)
        expired = jwt.encode(
            {**payload, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            PASSWORD_RESET_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        missing_claim = jwt.encode(
            {name: value for name, value in payload.items() if name != "jti"},
            PASSWORD_RESET_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        session = create_access_token(self.user["id"], self.user["email"], "user")
        for invalid in ["", "not-a-token", token + "tampered", expired, missing_claim, session]:
            with self.subTest(token=invalid[:12]):
                self.assertEqual(self.reset(invalid).status_code, 400)
        self.assertEqual(self.reset(token).status_code, 200)

    def test_token_expires_after_30_minutes(self):
        payload = decode_password_reset_token(self.request_token())
        remaining = payload["exp"] - datetime.now(timezone.utc).timestamp()
        self.assertGreater(remaining, 1790)
        self.assertLessEqual(remaining, 1800)

    def test_reset_token_cannot_authenticate(self):
        token = self.request_token()
        response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_new_request_invalidates_previous_token(self):
        previous = self.request_token()
        latest = self.request_token()
        self.assertNotEqual(previous, latest)
        self.assertEqual(self.reset(previous).status_code, 400)
        self.assertEqual(self.reset(latest).status_code, 200)

    def test_change_requires_valid_session(self):
        expired = create_access_token(self.user["id"], self.user["email"], "user", expires_minutes=-1)
        for headers in [{}, {"Authorization": "Bearer invalid"}, {"Authorization": f"Bearer {expired}"}]:
            response = self.client.post(
                "/auth/change-password",
                json={"current_password": "old-password", "new_password": "new-password"},
                headers=headers,
            )
            self.assertEqual(response.status_code, 401)

    def test_change_checks_current_password_and_invalidates_reset(self):
        token = self.request_token()
        headers = self.authorization()
        response = self.client.post(
            "/auth/change-password",
            json={"current_password": "incorrect", "new_password": "new-password"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password_reset_token_hash", users.get_user_auth_record(self.user["id"]))
        response = self.client.post(
            "/auth/change-password",
            json={"current_password": "old-password", "new_password": "new-password"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reset(token).status_code, 400)
        self.assertTrue(verify_password("new-password", users.get_user_auth_record(self.user["id"])["hashed_password"]))

    def test_new_password_validation(self):
        token = self.request_token()
        for password in ["short", "x" * 73, "\u00e9" * 37]:
            with self.subTest(password_length=len(password)):
                self.assertEqual(self.reset(token, password).status_code, 422)
                response = self.client.post(
                    "/auth/change-password",
                    json={"current_password": "old-password", "new_password": password},
                    headers=self.authorization(),
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.reset(token, "\u00e9" * 36).status_code, 200)

    def test_provider_failure_returns_200_and_removes_token(self):
        self.mail.side_effect = email.EmailDeliveryError("provider failure")
        with self.assertLogs("app.services.users", level="WARNING"):
            self.assertEqual(self.client.post("/auth/forgot-password", json={"email": self.user["email"]}).status_code, 200)
        token = self.mail.call_args.args[1]
        self.assertEqual(self.reset(token).status_code, 400)
        self.assertNotIn("password_reset_token_hash", users.get_user_auth_record(self.user["id"]))

    def test_inactive_user_gets_generic_response_without_email(self):
        users.update_user(self.user["id"], {"is_active": False})
        response = self.client.post("/auth/forgot-password", json={"email": self.user["email"]})
        self.assertEqual(response.status_code, 200)
        self.mail.assert_not_called()

    def test_account_changes_invalidate_tokens(self):
        for updates in [{"email": "changed@example.com"}, {"password": "updated-password"}, {"is_active": False}]:
            with self.subTest(updates=updates):
                token = self.request_token()
                users.update_user(self.user["id"], updates.copy())
                self.assertEqual(self.reset(token).status_code, 400)
                users.update_user(self.user["id"], {"email": self.user["email"], "is_active": True})

    def test_deleted_user_cannot_reset(self):
        token = self.request_token()
        users.delete_user(self.user["id"])
        self.assertEqual(self.reset(token).status_code, 400)

    def test_concurrent_reset_only_succeeds_once(self):
        token = self.request_token()

        def attempt_reset():
            try:
                users.reset_password(token, "new-password")
                return 200
            except users.InvalidPasswordResetError:
                return 400

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt_reset(), range(2)))
        self.assertEqual(sorted(results), [200, 400])

    def test_token_consumption_persists_after_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.json"
            database = TinyDB(path)
            with patch.multiple(users, users_table=database.table("users"), profiles_table=database.table("profiles")):
                account = users.create_user("persistent@example.com", "old-password")
                users.request_password_reset(account["email"])
                token = self.mail.call_args.args[1]
            database.close()
            database = TinyDB(path)
            with patch.object(users, "users_table", database.table("users")):
                users.reset_password(token, "new-password")
            database.close()
            database = TinyDB(path)
            try:
                with patch.object(users, "users_table", database.table("users")):
                    with self.assertRaises(users.InvalidPasswordResetError):
                        users.reset_password(token, "other-password")
            finally:
                database.close()


class TransactionalEmailTests(unittest.TestCase):
    def setUp(self):
        settings = {
            "RESEND_API_KEY": "test-only-api-key",
            "EMAIL_FROM": "TrackFlow <security@example.com>",
            "RESET_PASSWORD_URL": "https://app.example.com/reset-password?lang=es&token=old",
        }
        self.settings = patch.dict(os.environ, settings)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_sends_resend_request_with_mobile_html_and_plain_text(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch("app.services.email.urlopen", return_value=response) as transport:
            email.send_password_reset_email("owner@example.com", "token-with+special&characters")
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-api-key")
        self.assertEqual(transport.call_args.kwargs["timeout"], 10)
        message = json.loads(request.data)
        self.assertEqual(message["to"], ["owner@example.com"])
        self.assertIn('name="viewport"', message["html"])
        self.assertIn("font-size:16px", message["html"])
        self.assertIn("30 minutos", message["text"])
        self.assertIn("&amp;token=", message["html"])
        link = message["text"].split("Abre este enlace: ")[1].splitlines()[0]
        self.assertEqual(parse_qs(urlsplit(link).query), {"lang": ["es"], "token": ["token-with+special&characters"]})

    def test_requires_api_key_sender_and_frontend_url(self):
        for name in ["RESEND_API_KEY", "EMAIL_FROM", "RESET_PASSWORD_URL"]:
            with self.subTest(name=name), patch.dict(os.environ, {name: ""}):
                with self.assertRaises(email.EmailDeliveryError):
                    email.send_password_reset_email("owner@example.com", "token")

    def test_rejects_insecure_or_invalid_frontend_urls(self):
        for url in ["http://app.example.com/reset", "not-a-url", "https://user:password@example.com/reset"]:
            with self.subTest(url=url), patch.dict(os.environ, {"RESET_PASSWORD_URL": url}):
                with self.assertRaises(email.EmailDeliveryError):
                    email.send_password_reset_email("owner@example.com", "token")

    def test_converts_provider_errors_to_delivery_errors(self):
        for failure in [URLError("network"), TimeoutError(), HTTPError("https://api.resend.com/emails", 429, "Rate limited", {}, None)]:
            with self.subTest(failure=type(failure).__name__), patch("app.services.email.urlopen", side_effect=failure):
                with self.assertRaises(email.EmailDeliveryError):
                    email.send_password_reset_email("owner@example.com", "token")


if __name__ == "__main__":
    unittest.main()
import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.main import app
from app.models import (
    ApplicationSubmissionRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ProfileUpdateRequest,
    ResetPasswordRequest,
    RoleEnum,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.schemas import SKUCreateRequest, StockEntryCreateRequest, StockExitCreateRequest
from app.services import users


def test_message_schema_drops_credentials():
    response = MessageResponse.model_validate({
        "message": "ok", "email": "private@example.com",
        "password": "secret", "hashed_password": "hash",
        "password_reset_token_hash": "internal",
    })
    assert response.model_dump() == {"message": "ok"}


def test_registration_request_rejects_response_fields():
    for field in ("id", "role", "is_active", "created_at", "profile"):
        with pytest.raises(ValidationError):
            UserCreateRequest(email="new@example.com", password="valid-password", **{field: "forged"})


def test_user_list_is_flat_and_detail_profile_is_minimal(client, user, auth_headers):
    users.update_user(user["id"], {"role": RoleEnum.admin})
    response = client.get("/users", headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json()[0]) == {"id", "email", "is_active", "role"}
    response = client.get(f'/users/{user["id"]}', headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json()["profile"]) == {"name", "phone", "address"}


def test_profile_list_does_not_include_contact_details(client, user, auth_headers):
    users.update_user(user["id"], {"role": RoleEnum.admin})
    response = client.get("/profiles", headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json()[0]) == {"id", "user_id", "name"}


def test_user_detail_filters_injected_credentials(client, user, auth_headers):
    record = {
        **user, "hashed_password": "internal-hash", "password_reset_token_hash": "internal-token",
        "password": "plain-secret", "internal_api_key": "private-key",
        "profile": {**user["profile"], "hashed_password": "nested-hash"},
    }
    with patch("app.api.users.get_user_by_id_or_raise", return_value=record):
        response = client.get(f'/users/{user["id"]}', headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "is_active", "role", "created_at", "profile"}
    assert set(response.json()["profile"]) == {"name", "phone", "address"}
    for secret in ("internal-hash", "internal-token", "plain-secret", "private-key", "nested-hash"):
        assert secret not in response.text


@pytest.mark.parametrize("request_schema", [
    UserCreateRequest, UserUpdateRequest, ProfileUpdateRequest, LoginRequest,
    ForgotPasswordRequest, ResetPasswordRequest, ChangePasswordRequest,
    ApplicationSubmissionRequest, SKUCreateRequest, StockEntryCreateRequest, StockExitCreateRequest,
])
def test_user_api_write_schemas_forbid_extra_fields(request_schema):
    assert request_schema.model_config["extra"] == "forbid"


def test_auth_validation_does_not_echo_input_values(client):
    response = client.post("/auth/login", json={
        "email": "private@example.com", "password": "secret", "internal_token": "private-token",
    })
    assert response.status_code == 422
    for value in ("private@example.com", "secret", "private-token"):
        assert value not in response.text


def test_auth_me_keeps_own_email_without_redundant_relationship_ids(client, user, auth_headers):
    response = client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == user["email"]
    assert set(response.json()["profile"]) == {"name", "phone", "address"}


def test_password_responses_are_message_only(client, user, auth_headers):
    with patch("app.services.users.send_password_reset_email") as send_email:
        response = client.post("/auth/forgot-password", json={"email": user["email"]})
        assert set(response.json()) == {"message"}
        token = send_email.call_args.args[1]
    response = client.post("/auth/reset-password", json={"token": token, "new_password": "new-password"})
    assert response.status_code == 200
    assert set(response.json()) == {"message"}
    response = client.post("/auth/change-password", headers=auth_headers, json={
        "current_password": "new-password", "new_password": "next-password",
    })
    assert response.status_code == 200
    assert set(response.json()) == {"message"}


def test_user_and_profile_deletions_have_no_body(client, user, auth_headers):
    users.update_user(user["id"], {"role": RoleEnum.admin})
    response = client.delete(f'/profiles/user/{user["id"]}', headers=auth_headers)
    assert response.status_code == 204
    assert response.content == b""
    response = client.delete(f'/users/{user["id"]}', headers=auth_headers)
    assert response.status_code == 204
    assert response.content == b""


def test_root_health_and_openapi_contracts(client):
    assert client.get("/").json() == {
        "message": "TrackFlow User API running", "docs": "/docs", "health": "/health",
    }
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/users"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MessageResponse",
    }
    for route in app.routes:
        if isinstance(route, APIRoute):
            if route.status_code == 204:
                assert route.response_model is None
            else:
                assert route.response_model is not None


def test_all_production_route_decorators_declare_response_model():
    root = Path(__file__).resolve().parents[1]
    endpoints = 0
    for directory in (root / "services/user-api/app", root / "services/api"):
        for source in directory.rglob("*.py"):
            if "tests" in source.parts or source.name == "app.py":
                continue
            module = ast.parse(source.read_text())
            for node in ast.walk(module):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                        continue
                    if decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
                        continue
                    endpoints += 1
                    assert any(keyword.arg == "response_model" for keyword in decorator.keywords), source
    assert endpoints == 37
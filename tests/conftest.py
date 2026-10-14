import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from tinydb import TinyDB
from tinydb.storages import MemoryStorage


os.environ["JWT_SECRET_KEY"] = "pytest-only-secret-key"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
USER_API_ROOT = Path(__file__).resolve().parents[1] / "services" / "user-api"
test_database = TinyDB(storage=MemoryStorage)

with patch("tinydb.TinyDB", return_value=test_database):
    from app.main import app

from app.db import profiles_table
from app.security import create_access_token
from app.services import users


@pytest.fixture(autouse=True)
def clear_database():
    for table in test_database.tables():
        test_database.table(table).truncate()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user():
    def create(
        email="owner@example.com",
        password="old-password",
        name="Owner Example",
    ):
        return users.create_user(email=email, password=password, name=name)

    return create


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def auth_headers(user):
    token = create_access_token(user["id"], user["email"], user["role"])
    return {"Authorization": f"Bearer {token}"}
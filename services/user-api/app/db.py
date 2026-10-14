import os
from collections.abc import Generator
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine
from tinydb import TinyDB


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "db.json"

db = TinyDB(DB_PATH)
users_table = db.table("users")
profiles_table = db.table("profiles")
applications_table = db.table("applications")


def _create_postgres_engine(database_url: str):
	url = make_url(database_url)
	if url.get_backend_name() != "postgresql":
		raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
	url = url.set(drivername="postgresql+psycopg")
	if "sslmode" not in url.query:
		url = url.update_query_dict({"sslmode": "require"})
	return create_engine(url, pool_pre_ping=True)


database_url = os.getenv("DATABASE_URL")
engine = _create_postgres_engine(database_url) if database_url else None


def get_db() -> Generator[Session, None, None]:
	if engine is None:
		raise RuntimeError("Missing required environment variable: DATABASE_URL")
	with Session(engine) as session:
		yield session


DBSession = Annotated[Session, Depends(get_db)]

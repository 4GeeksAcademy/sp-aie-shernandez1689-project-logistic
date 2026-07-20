from pathlib import Path

from tinydb import TinyDB


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "db.json"

db = TinyDB(DB_PATH)
users_table = db.table("users")
profiles_table = db.table("profiles")

from pathlib import Path

from tinydb import TinyDB


DB_PATH = Path(__file__).resolve().parent / "data" / "tinydb.json"
TABLE_NAME = "suppliers"


def get_suppliers_table():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = TinyDB(DB_PATH)
    return db, db.table(TABLE_NAME)
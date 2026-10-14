import argparse

from sqlmodel import SQLModel, Session, select
from tinydb import Query

from app import db
from app.models import SKU, StockEntry, StockExit


SKU_SEED = [
    {"name": "Zapatilla blanca cl\u00e1sica - Talla 42", "sku": "CLT-SNK-W-42", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "LA"},
    {"name": "Zapatilla blanca cl\u00e1sica - Talla 42", "sku": "CLT-SNK-W-42-Z", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "ZGZ"},
    {"name": "Auriculares inal\u00e1mbricos Pro", "sku": "TEC-EAR-001", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "LA"},
    {"name": "S\u00e9rum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab Cosmetics", "category": "cosmetics", "warehouse": "ZGZ"},
    {"name": "Chino slim fit - marino 32/32", "sku": "CLT-CHN-N-32", "client_name": "UrbanThread", "category": "fashion", "warehouse": "LA"},
    {"name": "Cargador r\u00e1pido USB-C 65W", "sku": "TEC-CHG-065", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "ZGZ"},
]

ENTRY_SEED = [
    {"sku": "CLT-SNK-W-42", "quantity": 40, "reference": "PO-2024-0098"},
    {"sku": "CLT-SNK-W-42", "quantity": 15, "reference": "GR-LA-0234"},
    {"sku": "CLT-SNK-W-42-Z", "quantity": 25, "reference": "GR-ZGZ-0235"},
    {"sku": "TEC-EAR-001", "quantity": 30, "reference": "PO-2024-0099"},
    {"sku": "CSM-SRM-030", "quantity": 50, "reference": "GR-ZGZ-0236"},
    {"sku": "CLT-CHN-N-32", "quantity": 20, "reference": "GR-LA-0237"},
    {"sku": "TEC-CHG-065", "quantity": 35, "reference": "PO-2024-0100"},
]

EXIT_SEED = [
    {"sku": "CLT-SNK-W-42", "quantity": 12, "exit_type": "dispatch", "tracking_number": "1Z999AA10123456784"},
    {"sku": "CLT-SNK-W-42", "quantity": 3, "exit_type": "loss", "tracking_number": None},
    {"sku": "CLT-SNK-W-42-Z", "quantity": 5, "exit_type": "dispatch", "tracking_number": "1Z999AA10123456785"},
]


def seed_inventory(session: Session, user_uuid: str) -> dict[str, int]:
    user = db.users_table.get(Query().id == user_uuid)
    if not user or not user.get("is_active", True):
        raise ValueError("Seeding requires an active existing TinyDB user")

    with session.begin():
        if any(session.exec(select(model).limit(1)).first() is not None for model in (SKU, StockEntry, StockExit)):
            raise ValueError("Inventory must be empty; existing records were not modified")
        products = {}
        available = {}
        for fields in SKU_SEED:
            product = SKU(**fields)
            session.add(product)
            products[product.sku] = product
            available[(product.sku, product.warehouse)] = 0
        session.flush()

        for fields in ENTRY_SEED:
            product = products[fields["sku"]]
            session.add(StockEntry(
                sku_id=product.id, quantity=fields["quantity"], reference=fields["reference"],
                warehouse=product.warehouse, user_uuid=user_uuid,
            ))
            available[(product.sku, product.warehouse)] += fields["quantity"]
        session.flush()

        for fields in EXIT_SEED:
            product = products[fields["sku"]]
            partition = (product.sku, product.warehouse)
            if fields["quantity"] > available[partition]:
                raise ValueError("Seed exits cannot exceed entries in the SKU warehouse")
            session.add(StockExit(
                sku_id=product.id, quantity=fields["quantity"], exit_type=fields["exit_type"],
                tracking_number=fields["tracking_number"], warehouse=product.warehouse,
                user_uuid=user_uuid,
            ))
            available[partition] -= fields["quantity"]

    return {"skus": len(SKU_SEED), "entries": len(ENTRY_SEED), "exits": len(EXIT_SEED)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TrackFlow demo inventory in PostgreSQL")
    parser.add_argument("--user-uuid", help="UUID of an active existing TinyDB user")
    args = parser.parse_args()
    if db.engine is None:
        parser.error("DATABASE_URL must be configured before seeding inventory")
    user_uuid = args.user_uuid
    if not user_uuid:
        active_users = [user for user in db.users_table.all() if user.get("is_active", True)]
        if len(active_users) != 1:
            parser.error("Specify --user-uuid; automatic selection requires exactly one active TinyDB user")
        user_uuid = active_users[0]["id"]
    try:
        SQLModel.metadata.create_all(db.engine)
        with Session(db.engine) as session:
            result = seed_inventory(session, user_uuid)
    except ValueError as error:
        parser.exit(1, f"Inventory was not seeded: {error}\n")
    except Exception as error:
        parser.exit(1, f"Inventory was not seeded ({type(error).__name__}); check database configuration.\n")
    print(f"Seeded {result['skus']} SKUs, {result['entries']} entries and {result['exits']} exits.")


if __name__ == "__main__":
    main()
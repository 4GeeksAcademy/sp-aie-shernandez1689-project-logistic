"""Tests de la tool de consulta de inventario (inventory_lookup).

Cobertura del contrato:
- Entrada tipada: `InventoryQuery` valida sku_id/sku/filtros.
- Salida tipada: `InventoryLookupResult.product` replica los campos de
  SKUResponse (id, name, sku, client_name, category, warehouse,
  current_stock).
- Lee del gestor REAL de services/user-api (mismos modelos SQLModel y
  misma fórmula current_stock = Σentradas − Σsalidas que GET
  /inventory/products) — no de un dataset paralelo.
- Solo lectura: la tool nunca escribe en el gestor (test explícito).
- Timeout numérico explícito y fallback honesto (nunca inventa stock).
- Producto inexistente -> fallback honesto.

Corre:  uv run pytest tests/pipelines/test_inventory_lookup.py -q
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session, select

from services.rag_agent.tools.inventory_lookup import (
    INVENTORY_TIMEOUT_SECONDS,
    InventoryLookupResult,
    InventoryQuery,
    InventoryReader,
    _import_inventory_manager,
    lookup_inventory,
    reset_reader,
)

# Los modelos reales del gestor de inventario (services/user-api), cargados
# por ruta igual que hace la tool (misma clase que usa GET /inventory/products).
SKU, StockEntry, StockExit, SKUResponse = _import_inventory_manager()


SKU_SEED = [
    {"name": "Zapatilla blanca clásica - Talla 42", "sku": "CLT-SNK-W-42", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "LA"},
    {"name": "Zapatilla blanca clásica - Talla 42", "sku": "CLT-SNK-W-42-Z", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "ZGZ"},
    {"name": "Auriculares inalámbricos Pro", "sku": "TEC-EAR-001", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "LA"},
    {"name": "Cargador rápido USB-C 65W", "sku": "TEC-CHG-065", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "ZGZ"},
]
# (sku, quantity, kind) -- kind "entry" o "exit" (dispatch/loss) para que la
# suma de entradas menos salidas dé el current_stock que expone la API.
MOVEMENTS = [
    ("CLT-SNK-W-42", 40, "entry"),
    ("CLT-SNK-W-42", 15, "entry"),
    ("CLT-SNK-W-42", 12, "exit"),
    ("CLT-SNK-W-42", 3, "exit"),
    ("CLT-SNK-W-42-Z", 25, "entry"),
    ("CLT-SNK-W-42-Z", 5, "exit"),
    ("TEC-EAR-001", 30, "entry"),
    ("TEC-CHG-065", 35, "entry"),
]


@pytest.fixture
def session(tmp_path):
    """Base SQLModel aislada con los mismos modelos del gestor real.

    Reutiliza los modelos SQLModel de services/user-api (SKU/StockEntry/
    StockExit) y la misma fórmula de current_stock de la API; es la misma
    capa que lee GET /inventory/products (solo contra una base aislada).
    """
    engine = create_engine("sqlite://")
    # Solo las tablas de inventario del gestor: el `SQLModel.metadata` global
    # puede contener tablas de otros managers (p.ej. telemetry_events con
    # gen_random_uuid() de PostgreSQL) cuando otros tests importan esos
    # módulos; crear solo estas 3 tablas aísla el fixture de esa polución.
    SQLModel.metadata.create_all(
        engine, tables=[SKU.__table__, StockEntry.__table__, StockExit.__table__]
    )
    with Session(engine) as sess:
        skus = {}
        for fields in SKU_SEED:
            product = SKU(**fields)
            sess.add(product)
            skus[product.sku] = product
        sess.flush()
        for sku, quantity, kind in MOVEMENTS:
            product = skus[sku]
            if kind == "entry":
                sess.add(
                    StockEntry(
                        sku_id=product.id, quantity=quantity, reference=f"REF-{sku}",
                        warehouse=product.warehouse, user_uuid="u-1",
                    )
                )
            else:
                sess.add(
                    StockExit(
                        sku_id=product.id, quantity=quantity,
                        exit_type="dispatch" if quantity > 5 else "loss",
                        tracking_number="1Z999" if quantity > 5 else None,
                        warehouse=product.warehouse, user_uuid="u-1",
                    )
                )
        sess.commit()
        yield sess


@pytest.fixture
def reader(session):
    """Reader configurado con la sesión aislada (sin DATABASE_URL real)."""
    return InventoryReader(SKU=SKU, StockEntry=StockEntry, StockExit=StockExit, SKUResponse=SKUResponse, session=session)


@pytest.fixture
def lookup(reader, monkeypatch):
    """lookup_inventory() con el reader inyectado (misma sesión aislada).

    Evita que la función canónica intente abrir DATABASE_URL (que no está
    configurada en tests): el reader usa la sesión SQLite del fixture.
    """
    import services.rag_agent.tools.inventory_lookup as tool_mod

    monkeypatch.setattr(tool_mod, "_get_reader", lambda: reader)
    return tool_mod.lookup_inventory


@pytest.fixture(autouse=True)
def _reset_reader():
    reset_reader()
    yield
    reset_reader()


# ── Contrato de entrada tipado ──────────────────────────────────────────


class TestInventoryQueryContract:
    def test_accepts_sku_and_sku_id(self):
        InventoryQuery(sku="CLT-SNK-W-42")
        InventoryQuery(sku_id=3)

    def test_filters_are_optional(self):
        q = InventoryQuery(category="fashion", warehouse="LA")
        assert q.category == "fashion"
        assert q.warehouse == "LA"


# ── Salida tipada: mismos campos que SKUResponse ────────────────────────


class TestLookupResultContract:
    def test_result_exposes_sku_response_fields(self):
        """El resultado replica SKUResponse (id, name, sku, client_name,
        category, warehouse, current_stock) + metadatos de consulta."""
        product = {
            "id": 1,
            "name": "Zapatilla blanca clásica - Talla 42",
            "sku": "CLT-SNK-W-42",
            "client_name": "PureStep Footwear",
            "category": "fashion",
            "warehouse": "LA",
            "current_stock": 40,
        }
        result = InventoryLookupResult(product=product, resolved=True)
        assert result.product["sku"] == "CLT-SNK-W-42"
        assert result.product["current_stock"] == 40
        assert result.resolved is True


# ── Consulta real contra el gestor (en-proceso, sin HTTP) ───────────────


class TestLookupAgainstRealManager:
    def test_lookup_by_sku_success(self, reader, session):
        result = reader.read(InventoryQuery(sku="CLT-SNK-W-42"))
        assert result.resolved is True
        assert result.product["sku"] == "CLT-SNK-W-42"
        # 40 + 15 - 12 - 3 = 40 (misma fórmula que _current_stock de la API)
        assert result.product["current_stock"] == 40
        assert result.product["warehouse"] == "LA"

    def test_lookup_by_sku_id_success(self, reader, session):
        product_row = session.exec(select(SKU).where(SKU.sku == "TEC-EAR-001")).one()
        result = reader.read(InventoryQuery(sku_id=product_row.id))
        assert result.resolved is True
        assert result.product["current_stock"] == 30

    def test_lookup_by_unknown_sku_is_honest(self, reader, session):
        result = reader.read(InventoryQuery(sku="ZZZ-NOPE-99"))
        assert result.resolved is False
        assert "no existe" in (result.error or "")

    def test_lookup_by_unknown_sku_id_is_honest(self, reader, session):
        result = reader.read(InventoryQuery(sku_id=999999))
        assert result.resolved is False
        assert "no existe" in (result.error or "")

    def test_lookup_by_filter_single_match(self, reader, session):
        result = reader.read(InventoryQuery(category="electronics", warehouse="ZGZ"))
        assert result.resolved is True
        assert result.product["sku"] == "TEC-CHG-065"
        assert result.product["current_stock"] == 35

    def test_lookup_by_filter_multiple_matches_asks_for_sku(self, reader, session):
        result = reader.read(InventoryQuery(category="fashion"))
        assert result.resolved is False
        assert "especifica un sku_id o sku" in (result.error or "")

    def test_lookup_with_no_query_lists_count(self, reader, session):
        result = reader.read(InventoryQuery())
        assert result.resolved is True
        assert "4" in (result.description or "")

    def test_accepts_plain_string_sku(self, lookup):
        result = lookup("CLT-SNK-W-42")
        assert result.resolved is True
        assert result.product["sku"] == "CLT-SNK-W-42"


# ── Timeout y fallback ──────────────────────────────────────────────────


class TestTimeoutAndFallback:
    def test_explicit_numeric_timeout_is_defined(self):
        assert INVENTORY_TIMEOUT_SECONDS > 0
        assert isinstance(INVENTORY_TIMEOUT_SECONDS, float)

    def test_timeout_zero_returns_fallback(self, lookup):
        result = lookup({"sku": "CLT-SNK-W-42"}, timeout=0)
        assert result.resolved is False
        assert "no pude" in (result.error or "")

    def test_lookup_applies_timeout(self, lookup, monkeypatch):
        """Timeout vía SIGALRM real: la lectura que duerme dispara el fallback."""
        import signal

        import services.rag_agent.tools.inventory_lookup as tool_mod

        def slow_read(self, query):
            signal.pause()  # se bloquea hasta que la alarma lo interrumpe
            return InventoryLookupResult(resolved=True)

        monkeypatch.setattr(InventoryReader, "read", slow_read)
        monkeypatch.setattr(tool_mod, "INVENTORY_TIMEOUT_SECONDS", 0.2)
        result = lookup({"sku": "CLT-SNK-W-42"})
        assert result.resolved is False
        assert "no pude" in (result.error or "")


# ── Solo lectura ────────────────────────────────────────────────────────


class TestReadOnly:
    def test_lookup_does_not_write_to_the_manager(self, reader, session):
        before = len(session.exec(select(SKU)).all())
        reader.read(InventoryQuery(sku="CLT-SNK-W-42"))
        reader.read(InventoryQuery(category="fashion"))
        reader.read(InventoryQuery())
        after = len(session.exec(select(SKU)).all())
        assert before == after == len(SKU_SEED)
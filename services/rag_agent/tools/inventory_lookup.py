# ╔════════════════════════════════════════════════════════════════════╗
# ║  DEPRECATED — 2026-10-27                                          ║
# ║  Esta tool directa en-proceso ya NO la usa el agente LangGraph.   ║
# ║  El agente se conecta al TrackFlow MCP Server via                 ║
# ║  ``langchain-mcp-adapters`` (ver ``services/rag_agent/mcp_client.py``).  ║
# ║  Este módulo se conserva solo para compatibilidad con tests       ║
# ║  existentes. No usar en nuevo código.                             ║
# ╚════════════════════════════════════════════════════════════════════╝

"""Tool: consulta de inventario (stock por producto). [DEPRECATED]

DEPRECATED desde 2026-10-27. El agente ya no usa esta tool.
Ver ``services/rag_agent/tools/DEPRECATED.md`` para detalles.

This module is kept for test compatibility only. Do not use in new code.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from services.rag_agent.tools._timeout import run_with_timeout

# Raíz del monorepo: services/rag_agent/tools/inventory_lookup.py -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
_USER_API_APP_DIR = _REPO_ROOT / "services" / "user-api" / "app"

# Timeout explícito y numérico de la llamada (segundos). El grafo nunca queda
# colgado si el gestor de inventario no responde: igual que en incident_lookup.
INVENTORY_TIMEOUT_SECONDS: float = float(os.getenv("INVENTORY_LOOKUP_TIMEOUT", "4.0"))

# Nombres de módulo estables para los modelos del user-api (evitan la
# colisión del namespace app con services/api/app.py).
_INV_MODELS = "trackflow_userapi_models"
_INV_SCHEMAS = "trackflow_userapi_schemas"


class InventoryQuery(BaseModel):
    """Entrada tipada de la tool (contrato de entrada)."""

    sku_id: int | None = Field(default=None, description="ID entero del producto (GET /inventory/products/{id}).")
    sku: str | None = Field(default=None, description="Código de producto exacto (p. ej. CLT-SNK-W-42).")
    category: str | None = Field(default=None, description="Filtro: categoría del producto.")
    warehouse: str | None = Field(default=None, description="Filtro: almacén (LA | ZGZ).")
    client_name: str | None = Field(default=None, description="Filtro: nombre del cliente.")


class InventoryLookupResult(BaseModel):
    """Salida tipada de la tool (contrato de salida).

    ``product`` replica los campos de ``SKUResponse`` (id, name, sku,
    client_name, category, warehouse, current_stock) + metadatos de la
    consulta (``resolved``/``error``/``matched_filters``) para que el nodo
    del grafo decida el fallback.
    """

    product: dict[str, Any] | None = None
    description: str | None = None
    # metadatos de la consulta (no son campos del gestionador)
    resolved: bool = False
    error: str | None = None
    matched_filters: list[str] = Field(default_factory=list)


def _normalise_filters(query: InventoryQuery) -> tuple[dict[str, Any], list[str]]:
    """Devuelve el filtro normalizado y la lista de campos aplicados."""
    filters: dict[str, Any] = {}
    for field in ("category", "warehouse", "client_name"):
        value = getattr(query, field)
        if value:
            filters[field] = value.strip()
    return filters, sorted(filters.keys())


def _fallback(message: str) -> InventoryLookupResult:
    """Resultado honesto: el grafo no sabe el stock, no lo inventa."""
    return InventoryLookupResult(resolved=False, error=message)


def _load_module(name: str, filename: str) -> Any:
    """Carga un módulo del user-api por ruta bajo un nombre único.

    Evita el namespace ``app`` de services/user-api/app (que choca con el
    módulo services/api/app.py) y devuelve el módulo. Fallo de carga ->
    ImportError legible.
    """
    module_path = _USER_API_APP_DIR / filename
    if not module_path.exists():
        raise ImportError(f"no se encontró {module_path}")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no se pudo crear spec para {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import_inventory_manager() -> tuple[Any, Any, Any, Any]:
    """Importa en-proceso el gestor de inventario real de services/user-api.

    Devuelve (SKU, StockEntry, StockExit, SKUResponse). Los modelos son los
    mismos que usa app.routers.inventory para servir GET /inventory/products.
    """
    try:
        models = _load_module(_INV_MODELS, "models.py")
        schemas = _load_module(_INV_SCHEMAS, "schemas.py")
        return models.SKU, models.StockEntry, models.StockExit, schemas.SKUResponse
    except Exception as exc:  # pragma: no cover - defensivo
        raise ImportError(
            "No se pudo importar el gestor de inventario de services/user-api: "
            f"{exc}"
        ) from exc


class InventoryReader:
    """Lee del gestor de inventario en-proceso (solo SELECT).

    Acepta una sesión SQLModel inyectada (tests) o la crea con el motor de
    ``services/user-api/app/db.py`` (vía ``DATABASE_URL``) en producción.
    """

    def __init__(
        self,
        *,
        SKU: Any,
        StockEntry: Any,
        StockExit: Any,
        SKUResponse: Any,
        session: Any = None,
    ):
        self.SKU = SKU
        self.StockEntry = StockEntry
        self.StockExit = StockExit
        self.SKUResponse = SKUResponse
        self._session = session

    def _make_session(self) -> Any:
        """Crea una sesión SQLModel contra la misma base que la API.

        Carga ``services/user-api/app/db.py`` por ruta (mismo truco que los
        modelos) para evitar la colisión del namespace ``app``; el módulo
        expone ``engine`` construido desde ``DATABASE_URL``.
        """
        from sqlmodel import Session  # imports locales

        db = _load_module("trackflow_userapi_db", "db.py")
        if db.engine is None:
            raise ImportError(
                "DATABASE_URL no está configurada; no se puede conectar al "
                "gestor de inventario (services/user-api). No hay datos inventados."
            )
        return Session(db.engine)

    def _current_stock(self, session: Any, sku_id: int, warehouse: str) -> int:
        """Mismo cálculo que app.routers.inventory._current_stock.

        current_stock = Σ entradas − Σ salidas para ese producto/almacén.
        """
        from sqlalchemy import func
        from sqlmodel import select

        entries = (
            select(func.coalesce(func.sum(self.StockEntry.quantity), 0))
            .where(self.StockEntry.sku_id == sku_id, self.StockEntry.warehouse == warehouse)
            .scalar_subquery()
        )
        exits = (
            select(func.coalesce(func.sum(self.StockExit.quantity), 0))
            .where(self.StockExit.sku_id == sku_id, self.StockExit.warehouse == warehouse)
            .scalar_subquery()
        )
        return session.exec(select(entries - exits)).one()

    def _to_product(self, session: Any, product: Any) -> dict[str, Any]:
        """Replica SKUResponse.model_validate({**product.model_dump(),
        "current_stock": ...}) del router de inventario."""
        response = self.SKUResponse.model_validate(
            {
                **product.model_dump(),
                "current_stock": self._current_stock(session, product.id, product.warehouse),
            }
        )
        return response.model_dump(mode="json")

    def read(self, query: InventoryQuery) -> InventoryLookupResult:
        """Lee del gestor de inventario (solo lectura)."""
        query = InventoryQuery.model_validate(query.model_dump())
        filters, matched = _normalise_filters(query)
        from sqlmodel import select

        owns_session = self._session is None
        session = self._session if not owns_session else self._make_session()
        try:
            products = session.exec(select(self.SKU).order_by(self.SKU.id)).all()

            # 1. Consulta por id exacto (GET /inventory/products/{id}).
            if query.sku_id is not None:
                for product in products:
                    if product.id == query.sku_id:
                        return InventoryLookupResult(
                            product=self._to_product(session, product),
                            resolved=True,
                            matched_filters=matched,
                        )
                return _fallback(f"el producto con id '{query.sku_id}' no existe")

            # 2. Consulta por código de producto exacto.
            if query.sku:
                sku = query.sku.strip()
                for product in products:
                    if product.sku == sku:
                        return InventoryLookupResult(
                            product=self._to_product(session, product),
                            resolved=True,
                            matched_filters=matched,
                        )
                return _fallback(f"el producto '{sku}' no existe")

            # 3. Consulta con filtros (GET /inventory/products?category=&...).
            if filters:
                matches = [
                    product
                    for product in products
                    if all(getattr(product, f) == v for f, v in filters.items())
                ]
                if not matches:
                    joined = ", ".join(f"{k}={v}" for k, v in filters.items())
                    return _fallback(f"no hay productos con: {joined}")
                if len(matches) > 1:
                    joined = ", ".join(f"{k}={v}" for k, v in filters.items())
                    return _fallback(
                        f"hay {len(matches)} productos con: {joined} — especifica un sku_id o sku"
                    )
                return InventoryLookupResult(
                    product=self._to_product(session, matches[0]),
                    resolved=True,
                    matched_filters=matched,
                )

            # 4. Sin filtros: toda la lista (GET /inventory/products).
            if len(products) == 1:
                return InventoryLookupResult(
                    product=self._to_product(session, products[0]),
                    resolved=True,
                    matched_filters=[],
                )
            return InventoryLookupResult(
                description=f"{len(products)} productos registrados",
                resolved=True,
                matched_filters=[],
            )

        finally:
            if owns_session:
                session.close()


# Sistema de lectura del módulo (import lazy en primera llamada).
_reader: InventoryReader | None = None


def _get_reader() -> InventoryReader:
    global _reader
    if _reader is None:
        SKU, StockEntry, StockExit, SKUResponse = _import_inventory_manager()
        _reader = InventoryReader(
            SKU=SKU, StockEntry=StockEntry, StockExit=StockExit, SKUResponse=SKUResponse
        )
    return _reader


def reset_reader() -> None:
    """Reinicia el lector perezoso (útil en tests)."""
    global _reader
    _reader = None


def lookup_inventory(
    query: InventoryQuery | dict[str, Any] | str,
    *,
    timeout: float | None = None,
) -> InventoryLookupResult:
    """Forma canónica de la tool: consulta tipada con timeout explícito.

    Acepta un InventoryQuery, un dict con sus campos o un sku suelto.
    Aplica el timeout numérico (default INVENTORY_TIMEOUT_SECONDS) y
    convierte cualquier fallo a un InventoryLookupResult no resuelto.

    Solo lectura: esta función no escribe nada.
    """
    if isinstance(query, str):
        query = InventoryQuery(sku=query)
    try:
        if isinstance(query, dict):
            query = InventoryQuery.model_validate(query)
        timeout_s = timeout if timeout is not None else INVENTORY_TIMEOUT_SECONDS
        return run_with_timeout(lambda: _get_reader().read(query), timeout_s)
    except ValidationError as exc:
        return _fallback(f"entrada inválida: {exc.errors()[0]['msg']}")
    except TimeoutError:
        return _fallback(
            f"el gestor de inventario no respondió en {timeout_s:.1f}s — no pude "
            "confirmar el stock de ese producto ahora mismo"
        )
    except ImportError as exc:
        return _fallback(str(exc))
    except Exception as exc:  # pragma: no cover - defensivo
        return _fallback(f"fallo inesperado consultando inventario: {exc}")
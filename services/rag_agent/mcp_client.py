"""Cliente MCP para el agente RAG de TrackFlow.

Conecta con el TrackFlow MCP Server (mcps/trackflow-server) via
``langchain-mcp-adapters`` y expone las tools como funciones síncronas
que el grafo LangGraph puede invocar desde sus nodos.

Reemplaza la implementación anterior de tools directas en-proceso
(``services/rag_agent/tools/incident_lookup.py`` e
``inventory_lookup.py``). El agente ahora habla con el Incidents Manager
a través del MCP Server autenticado, no importando módulos de repositorio
directamente.

Uso
---
    from services.rag_agent.mcp_client import (
        lookup_incidents_mcp,
        lookup_inventory_mcp,
        reset_mcp_client,
    )

    # Consulta de tickets
    result = lookup_incidents_mcp({"ticket_id": "..."})
    result = lookup_incidents_mcp({"status": "open", "branch": "la_warehouse"})

    # Consulta de inventario
    result = lookup_inventory_mcp({"sku": "CLT-SNK-W-42"})
    result = lookup_inventory_mcp({"warehouse": "LA"})

Contrato de salida
------------------
Cada función devuelve un dict con los mismos campos que los antiguos
``IncidentLookupResult`` / ``InventoryLookupResult``, serializado a JSON,
para que el resto del grafo no necesite cambios.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────────────────

# URL base del MCP Server (transporte SSE: http://host:port/mcp).
_MCP_SERVER_URL: str = os.getenv(
    "MCP_SERVER_URL",
    "http://127.0.0.1:8100",
)

# Nombre del MCP server registrado en el cliente (para multi-server).
_MCP_SERVER_NAME: str = os.getenv(
    "MCP_SERVER_NAME",
    "trackflow",
)

# Bearer token para autenticación contra el MCP Server.
# Si no se configura, el cliente se conecta sin token (útil en desarrollo/test).
_MCP_TOKEN: str | None = os.getenv("MCP_TOKEN") or None

# Timeout para cada llamada a tool (segundos).
_MCP_TOOL_TIMEOUT: float = float(os.getenv("MCP_TOOL_TIMEOUT", "10.0"))


# ── Cliente singleton ──────────────────────────────────────────────────

_client: MultiServerMCPClient | None = None
_tools: dict[str, Any] | None = None  # cache: name -> LangChain BaseTool


def _get_client() -> MultiServerMCPClient:
    """Devuelve (y crea si es necesario) el cliente MCP singleton.

    El cliente se conecta al MCP Server usando el protocolo SSE/HTTP.
    Las tools se cargan bajo demanda en ``_get_tools()``.
    """
    global _client
    if _client is not None:
        return _client

    headers: dict[str, str] = {}
    if _MCP_TOKEN:
        headers["Authorization"] = f"Bearer {_MCP_TOKEN}"

    _client = MultiServerMCPClient(
        {
            _MCP_SERVER_NAME: {
                "transport": "http",
                "url": _MCP_SERVER_URL.rstrip("/") + "/mcp",
                "headers": headers if headers else None,
            },
        },
    )
    logger.info(
        "MCP client created: server=%s url=%s",
        _MCP_SERVER_NAME,
        _MCP_SERVER_URL,
    )
    return _client


async def _load_tools() -> dict[str, Any]:
    """Carga las tools del MCP Server de forma asíncrona.

    Returns:
        Dict con nombre -> LangChain BaseTool.
    """
    global _tools
    if _tools is not None:
        return _tools

    client = _get_client()
    tool_list = await client.get_tools(server_name=_MCP_SERVER_NAME)
    _tools = {tool.name: tool for tool in tool_list}
    logger.info("Loaded %d MCP tools: %s", len(_tools), list(_tools.keys()))
    return _tools


def _get_tools() -> dict[str, Any]:
    """Carga las tools MCP de forma síncrona (envuelve la async con asyncio.run)."""
    return asyncio.run(_load_tools())


# ── Funciones helper para invocar tools ────────────────────────────────

_INCIDENT_TOOLS = frozenset({"get_incident", "list_incidents"})
_INVENTORY_TOOLS = frozenset({"get_product", "list_products"})


async def _call_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoca una tool MCP de forma asíncrona y devuelve el resultado.

    La tool se obtiene del cache (``_tools``), que se carga la primera vez.
    El resultado del MCP se devuelve como dict.

    Raises:
        ValueError: si la tool no está disponible o el resultado es error.
    """
    tools = await _load_tools()
    tool = tools.get(tool_name)
    if tool is None:
        available = ", ".join(tools.keys()) if tools else "(no tools loaded)"
        raise ValueError(
            f"MCP tool '{tool_name}' not found. Available: {available}"
        )

    result = await tool.ainvoke(args)
    return _normalize_tool_result(result)


def _call_tool_sync(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoca una tool MCP de forma síncrona.

    Usa ``asyncio.run()`` internamente porque las MCP tools son async
    y el grafo LangGraph actual es síncrono.
    """
    return asyncio.run(_call_tool(tool_name, args))


def _normalize_tool_result(result: Any) -> dict[str, Any]:
    """Normaliza el resultado de una tool MCP a un dict.

    El MCP devuelve ``list[TextContent]``. Extraemos el texto y lo
    parseamos como JSON si es posible, o lo envolvemos en un dict
    con la estructura esperada por el grafo.
    """
    if isinstance(result, dict):
        return result

    if isinstance(result, list):
        # MCP SDK devuelve list[TextContent | ...]
        texts: list[str] = []
        for item in result:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
        if texts:
            raw = texts[0]
            import json as _json
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"result": parsed, "_raw": raw}
            except (ValueError, TypeError):
                return {"result": raw, "_raw": raw}

    return {"result": result}


# ── Fallback honesto ──────────────────────────────────────────────────

def _fallback(message: str) -> dict[str, Any]:
    """Resultado honesto: el agente no sabe, no inventa."""
    return {"resolved": False, "error": message}


# ======================================================================
# API pública — reemplaza lookup_incidents / lookup_inventory
# ======================================================================


def lookup_incidents_mcp(query: dict[str, Any]) -> dict[str, Any]:
    """Consulta de tickets/incidencias via MCP Server.

    Acepta los mismos campos que el antiguo ``IncidentQuery``:

    - ``ticket_id``: id exacto del ticket (usa ``get_incident``).
    - ``status`` / ``origin`` / ``branch`` / ``category``: filtros
      (usa ``list_incidents``).

    Devuelve un dict con la misma estructura que el antiguo
    ``IncidentLookupResult.model_dump(mode="json")`` para que el
    nodo del grafo no necesite cambios.

    Timeout: si el MCP no responde, devuelve fallback honesto
    (igual que la tool anterior).
    """
    ticket_id = query.get("ticket_id")
    try:
        if ticket_id:
            result = _call_tool_sync("get_incident", {"incident_id": ticket_id})
            if isinstance(result, dict) and result.get("error"):
                return _fallback(str(result["error"]))
            return _incident_to_result(result, ticket_id)
        else:
            # Construir filtros para list_incidents
            filters: dict[str, str] = {}
            for key in ("status", "origin", "branch", "category"):
                val = query.get(key)
                if val:
                    filters[key] = str(val).strip()
            result = _call_tool_sync("list_incidents", filters)
            return _list_incidents_to_result(result, filters)
    except TimeoutError:
        return _fallback(
            "el MCP Server no respondió a tiempo — no pude confirmar "
            "el estado de ese ticket ahora mismo"
        )
    except Exception as exc:
        logger.exception("Error calling MCP tool get_incident/list_incidents: %s", exc)
        return _fallback(f"fallo consultando incidencias via MCP: {exc}")


def _incident_to_result(incident: dict[str, Any], ticket_id: str | None) -> dict[str, Any]:
    """Convierte el resultado de get_incident al formato del antiguo
    IncidentLookupResult."""
    return {
        "ticket_id": ticket_id or incident.get("id"),
        "status": incident.get("status"),
        "category": incident.get("category"),
        "origin": incident.get("origin"),
        "branch": incident.get("branch"),
        "title": incident.get("title"),
        "description": incident.get("description"),
        "created_at": str(incident.get("created_at", "")),
        "updated_at": str(incident.get("updated_at", "")),
        "resolved": True,
        "error": None,
        "matched_filters": [],
    }


def _list_incidents_to_result(
    incidents: list[dict[str, Any]] | dict[str, Any],
    filters: dict[str, str],
) -> dict[str, Any]:
    """Convierte el resultado de list_incidents al formato esperado.

    Si la lista tiene exactamente un elemento, se devuelve como resultado
    resuelto (compatible con el comportamiento anterior de la tool).
    Si tiene varios, se pide ticket_id (fallback honesto). Si está vacía,
    se devuelve un fallback.
    """
    if isinstance(incidents, dict) and incidents.get("error"):
        joined = ", ".join(f"{k}={v}" for k, v in filters.items()) if filters else "sin filtros"
        return _fallback(f"no hay incidencias con: {joined}")

    if not isinstance(incidents, list):
        return _fallback("respuesta inesperada del servidor")

    matched_filters = sorted(filters.keys())

    if len(incidents) == 0:
        joined = ", ".join(f"{k}={v}" for k, v in filters.items()) if filters else "sin filtros"
        return _fallback(f"no hay incidencias con: {joined}")

    if len(incidents) > 1:
        joined = ", ".join(f"{k}={v}" for k, v in filters.items())
        return _fallback(
            f"hay {len(incidents)} incidencias con: {joined} — especifica un ticket_id"
        )

    return _incident_to_result(incidents[0], None)


def lookup_inventory_mcp(query: dict[str, Any]) -> dict[str, Any]:
    """Consulta de inventario/stock via MCP Server.

    Acepta los mismos campos que el antiguo ``InventoryQuery``:

    - ``sku_id``: id entero del producto (usa ``get_product``).
    - ``sku``: código de producto exacto.
    - ``warehouse`` / ``category`` / ``client_name``: filtros.

    NOTA: El MCP Server ``list_products`` solo soporta filtro por
    ``warehouse``. Si se pasan otros filtros (``category``,
    ``client_name``), se delega en ``list_products`` sin filtro
    adicional y se filtran en el cliente para mantener compatibilidad
    con el contrato anterior.

    Devuelve un dict con la misma estructura que el antiguo
    ``InventoryLookupResult.model_dump(mode="json")``.
    """
    sku_id = query.get("sku_id")
    sku = query.get("sku")

    try:
        if sku_id is not None:
            result = _call_tool_sync("get_product", {"product_id": int(sku_id)})
            if isinstance(result, dict) and result.get("error"):
                return _inventory_fallback(f"el producto con id '{sku_id}' no existe")
            return _product_to_result(result)

        if sku:
            # list_products y filtrar por SKU en cliente
            result = _call_tool_sync("list_products", {})
            return _find_product_by_sku(result, sku)

        # Filtros: warehouse, category, client_name
        warehouse = query.get("warehouse")
        result = _call_tool_sync(
            "list_products",
            {"warehouse": str(warehouse).strip()} if warehouse else {},
        )
        return _filter_products(result, query)

    except TimeoutError:
        return _inventory_fallback(
            "el MCP Server no respondió a tiempo — no pude consultar "
            "el stock ahora mismo"
        )
    except Exception as exc:
        logger.exception("Error calling MCP tool get_product/list_products: %s", exc)
        return _inventory_fallback(f"fallo consultando inventario via MCP: {exc}")


def _product_to_result(product: dict[str, Any]) -> dict[str, Any]:
    """Convierte un producto del MCP al formato InventoryLookupResult."""
    return {
        "product": product,
        "description": None,
        "resolved": True,
        "error": None,
        "matched_filters": [],
    }


def _inventory_fallback(message: str) -> dict[str, Any]:
    return {"product": None, "description": None, "resolved": False, "error": message, "matched_filters": []}


def _find_product_by_sku(
    products: list[dict[str, Any]] | dict[str, Any],
    sku: str,
) -> dict[str, Any]:
    """Busca un producto por su código SKU en la lista devuelta por list_products."""
    if not isinstance(products, list):
        return _inventory_fallback(f"el producto '{sku}' no existe")
    sku = sku.strip()
    for product in products:
        if product.get("sku") == sku:
            return _product_to_result(product)
    return _inventory_fallback(f"el producto '{sku}' no existe")


def _filter_products(
    products: list[dict[str, Any]] | dict[str, Any],
    query: dict[str, Any],
) -> dict[str, Any]:
    """Filtra productos en cliente (por category, client_name, etc.).

    list_products del MCP solo soporta warehouse como filtro. Los filtros
    extra se aplican localmente para mantener compatibilidad.
    """
    if not isinstance(products, list):
        return _inventory_fallback("no hay productos disponibles")

    matched_filters: list[str] = []
    filtered = list(products)

    warehouse = query.get("warehouse")
    if warehouse:
        warehouse_s = str(warehouse).strip()
        filtered = [p for p in filtered if p.get("warehouse") == warehouse_s]
        matched_filters.append("warehouse")

    category = query.get("category")
    if category:
        category_s = str(category).strip()
        filtered = [p for p in filtered if p.get("category") == category_s]
        matched_filters.append("category")

    client_name = query.get("client_name")
    if client_name:
        cn_s = str(client_name).strip()
        filtered = [p for p in filtered if p.get("client_name") == cn_s]
        matched_filters.append("client_name")

    if not filtered:
        joined = ", ".join(f"{k}={v}" for k, v in query.items() if v is not None)
        return _inventory_fallback(f"no hay productos con: {joined}")

    if len(filtered) > 1:
        joined = ", ".join(f"{k}={v}" for k, v in query.items() if v is not None)
        return _inventory_fallback(
            f"hay {len(filtered)} productos con: {joined} — especifica un sku_id o sku"
        )

    return _product_to_result(filtered[0])


# ── Reset para tests ──────────────────────────────────────────────────


def reset_mcp_client() -> None:
    """Reinicia el cliente MCP singleton y la cache de tools.

    Útil en tests para forzar una reconexión con configuraciones
    diferentes (p. ej. un MCP Server mockeado).
    """
    global _client, _tools
    _client = None
    _tools = None
    logger.debug("MCP client reset")

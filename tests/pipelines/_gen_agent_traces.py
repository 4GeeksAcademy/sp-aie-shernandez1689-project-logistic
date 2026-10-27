"""Genera los traces-fixture que consumen los evals del agente RAG.

Cada escenario ejecuta el grafo real (services.rag_agent.graph) con las
dependencias externas mockeadas (Qdrant, LLM y MCP tools), igual que
tests/pipelines/test_rag.py. El resultado es un trace JSON persistido en
data/eval/agent_traces/<thread_id>.json con el mismo schema que produciría
una corrida con servicios vivos.

Escenarios:
  1. eval_policy_discount — pregunta de política conocida; el chunk viene
     del company-knowledge-base real (trackflow-storage-pricing.es.md).
  2. eval_empty_question  — pregunta vacía -> rama error, sin retrieve.
  3. eval_no_context      — retrieve sin hits -> no_context, sin generate.
  4. eval_ticket_lookup — consulta de ticket usando el gestor TinyDB real,
      resuelta solo con tool (sin retrieve).
  5. eval_inventory_lookup — consulta de stock usando el gestor SQLModel
      real sobre SQLite aislada (sin mock de la tool).
  6. eval_routing_tool_only_ticket — eval nuevo de enrutamiento tool-only.
  7. eval_routing_rag_only_returns — eval nuevo de enrutamiento RAG-only.

NOTA: Las tools del MCP se parchean con las implementaciones en-proceso
(``tools/incident_lookup``, ``tools/inventory_lookup``) porque el grafo ya
no las importa directamente. Así la generación de traces eval sigue
funcionando sin un MCP Server vivo.

Ejecución: uv run python tests/pipelines/_gen_agent_traces.py
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlmodel import SQLModel, Session, create_engine

from services.rag_agent.graph import graph
from services.rag_agent import tracing
from services.rag_agent.mcp_client import reset_mcp_client
from services.rag_agent.tools import inventory_lookup as inventory_tool
from services.rag_agent.tools.inventory_lookup import InventoryReader, _import_inventory_manager
# NOTA: tools/incident_lookup e inventory_lookup se siguen usando aquí para
# sembrar datos y crear wrappers de compatibilidad (wrap_* abajo) porque el
# generador de traces no puede depender de un MCP Server vivo.

from services.rag_agent.tools.incident_lookup import lookup_incidents, IncidentQuery


# ── Wrappers de compatibilidad: redirigen MCP -> tools en-proceso ────────
# El grafo ahora usa lookup_incidents_mcp / lookup_inventory_mcp, pero el
# generador de traces eval necesita alimentar las tools con datos reales
# (TinyDB / SQLite aislada). Estos wrappers convierten la llamada MCP a la
# vieja tool directa y normalizan la salida a un dict plano (el mismo schema
# que devolvería el MCP Server).


def _wrap_incident_lookup_mcp(query: dict) -> dict:
    """Wrapper que llama lookup_incidents() y normaliza a dict plano."""
    q = IncidentQuery(**query)
    result = lookup_incidents(q)
    return result.model_dump(mode="json")


def _wrap_inventory_lookup_mcp(query: dict) -> dict:
    """Wrapper que llama lookup_inventory() y normaliza a dict plano."""
    from services.rag_agent.tools.inventory_lookup import lookup_inventory, InventoryQuery

    q = InventoryQuery(**query)
    result = lookup_inventory(q)
    return result.model_dump(mode="json")


REPO_ROOT = Path(__file__).resolve().parents[2]
KB_DOC = REPO_ROOT / "docs" / "company-knowledge-base" / "trackflow-storage-pricing.es.md"

# Id de incidencia de evaluación: lost_parcel abierto, origen branch (sucursal
# la_warehouse) — el mismo que usa test_agent_graph_evals.py.
EVAL_TICKET_ID = "4bccd74e-f1ee-4883-8985-cf73ca332b8c"

# Producto real del seed de services/user-api (seed_inventory.py). La cifra
# coincide con current_stock = Σentradas − Σsalidas que expone la API.
EVAL_INVENTORY_PRODUCT = {
    "name": "Zapatilla blanca clásica - Talla 42",
    "sku": "CLT-SNK-W-42",
    "client_name": "PureStep Footwear",
    "category": "fashion",
    "warehouse": "LA",
}

EVAL_TICKET_PAYLOAD = {
    "id": EVAL_TICKET_ID,
    "title": "Paquete perdido en LA Warehouse",
    "description": "El paquete #12345 no aparece en el inventario del almacén de LA.",
    "category": "lost_parcel",
    "status": "open",
    "origin": "branch",
    "branch": "la_warehouse",
}


@contextlib.contextmanager
def _seeded_incident_manager(tmp: Path):
    """Apunta services/api a una TinyDB temporal con la incidencia de eval.

    La tool incident_lookup lee en-proceso el gestor real
    (``database.get_incidents_table``), que usa ``database.DB_PATH`` en
    tiempo de llamada. Redirigimos ese path a un archivo temporal y
    sembramos la incidencia con id fijo, igual que hace create_incident
    (``Incident(**payload).model_dump(mode="json")``). Así la generación es
    reproducible y no toca ``services/api/data/tinydb.json``.
    """
    api_dir = REPO_ROOT / "services" / "api"
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))
    import database
    from models import Incident

    original = database.DB_PATH
    database.DB_PATH = tmp / "tinydb.json"
    db, table = database.get_incidents_table()
    try:
        table.insert(Incident(**EVAL_TICKET_PAYLOAD).model_dump(mode="json"))
    finally:
        db.close()
    try:
        yield database
    finally:
        database.DB_PATH = original
        reset_mcp_client()


@contextlib.contextmanager
def _seeded_inventory_manager():
    """Instala el reader real sobre tablas aisladas del gestor SQLModel.

    La tool lookup_inventory ejecuta su implementación real (SELECTs y
    cálculo de stock); solo se aísla la base de datos para hacer reproducible
    el eval sin DATABASE_URL ni datos externos.
    """
    SKU, StockEntry, StockExit, SKUResponse = _import_inventory_manager()
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine, tables=[SKU.__table__, StockEntry.__table__, StockExit.__table__]
    )
    session = Session(engine)
    original_reader = inventory_tool._reader
    try:
        product = SKU(**EVAL_INVENTORY_PRODUCT)
        session.add(product)
        session.flush()
        session.add_all(
            [
                StockEntry(sku_id=product.id, quantity=40, reference="EVAL-ENTRY", warehouse="LA", user_uuid="eval-user"),
                StockEntry(sku_id=product.id, quantity=15, reference="EVAL-ENTRY-2", warehouse="LA", user_uuid="eval-user"),
                StockExit(sku_id=product.id, quantity=12, exit_type="dispatch", tracking_number="EVAL-TRACKING", warehouse="LA", user_uuid="eval-user"),
                StockExit(sku_id=product.id, quantity=3, exit_type="loss", tracking_number=None, warehouse="LA", user_uuid="eval-user"),
            ]
        )
        session.commit()
        inventory_tool._reader = InventoryReader(
            SKU=SKU, StockEntry=StockEntry, StockExit=StockExit,
            SKUResponse=SKUResponse, session=session,
        )
        yield
    finally:
        inventory_tool._reader = original_reader
        session.close()
        engine.dispose()
        reset_mcp_client()


def _policy_chunk() -> dict:
    """Fragmento real del company-knowledge-base (política de descuentos)."""
    text = KB_DOC.read_text(encoding="utf-8")
    start = text.index("Cualquier descuento fuera de los rangos anteriores")
    snippet = text[start : start + 200].split("\n\n")[0]
    return {
        "text": snippet,
        "source_document": "trackflow-storage-pricing",
        "section": "Descuentos por volumen",
        "score": 0.87,
        "language": "es",
    }


def _returns_chunk() -> dict:
    """Fragmento real del KB de devoluciones."""
    path = REPO_ROOT / "docs" / "company-knowledge-base" / "trackflow-returns-policy.es.md"
    text = path.read_text(encoding="utf-8")
    start = text.index("TrackFlow ofrece una ventana de devolución")
    snippet = text[start : start + 180].split("\n\n")[0]
    return {
        "text": snippet,
        "source_document": "trackflow-returns-policy",
        "section": "Ventana de devolución",
        "score": 0.88,
        "language": "es",
    }


def _run(question: str, thread_id: str, *, hits: list | None, llm_answer: str | None) -> dict:
    """Ejecuta el grafo con mocks y devuelve su trace persistido."""
    client = MagicMock()
    client.search.return_value = [
        MagicMock(payload={k: v for k, v in h.items() if k != "score"}, score=h["score"])
        for h in (hits or [])
    ]

    llm_resp = MagicMock()
    llm_resp.raise_for_status.return_value = None
    llm_resp.json.return_value = {"choices": [{"message": {"content": llm_answer}}]}
    llm_client = MagicMock()
    llm_client.__enter__.return_value = llm_client
    llm_client.post.return_value = llm_resp

    with (
        patch("data.pipelines.rag.embed", return_value=[0.1] * 8),
        patch("data.pipelines.rag._get_qdrant", return_value=client),
        patch("data.pipelines.rag.httpx.Client", return_value=llm_client),
        patch(
            "services.rag_agent.mcp_client.lookup_incidents_mcp",
            side_effect=_wrap_incident_lookup_mcp,
        ),
        patch(
            "services.rag_agent.mcp_client.lookup_inventory_mcp",
            side_effect=_wrap_inventory_lookup_mcp,
        ),
    ):
        graph.invoke({"question": question}, {"configurable": {"thread_id": thread_id}})

    return tracing.load_trace(thread_id)


def main() -> None:
    # 1. Pregunta anclada al KB: retrieve trajo el chunk real y el LLM
    #    respondió citando la política (Miguel Torres).
    t1 = _run(
        "¿Qué aprobación requiere un descuento fuera de los rangos documentados?",
        "eval_policy_discount",
        hits=[_policy_chunk()],
        llm_answer=(
            "Cualquier descuento fuera de los rangos documentados requiere "
            "aprobación de Miguel Torres (Dirección Comercial). "
            "[Fuente: trackflow-storage-pricing / Descuentos por volumen]"
        ),
    )
    assert t1["final_state"].get("answer"), "trace 1 sin respuesta"

    # 2. Pregunta vacía: nunca llega a retrieve.
    t2 = _run("", "eval_empty_question", hits=[], llm_answer=None)
    assert "Error" in t2["final_state"]["answer"]

    # 3. Sin contexto sobre el umbral: honestidad, sin generate.
    t3 = _run(
        "¿Cuánto cuesta el frío interplanetario?",
        "eval_no_context",
        hits=[],
        llm_answer=None,
    )
    assert "no tengo información" in t3["final_state"]["answer"].lower()

    # 4. Consulta de ticket: la tool incident_lookup lee el gestor real
    #    en-proceso. retrieve/generate están mockeados; con hits=[] retrieve
    #    no aporta contexto KB, así que la respuesta final es el resumen de la
    #    tool (generate usa el camino tool-only, sin llamar al LLM). Sembramos
    #    la incidencia en una TinyDB temporal (id fijo) para que la generación
    #    sea reproducible sin tocar services/api/data/tinydb.json.
    with tempfile.TemporaryDirectory() as tmp:
        with _seeded_incident_manager(Path(tmp)):
            reset_mcp_client()
            t4 = _run(
                f"¿cuál es el estado del ticket {EVAL_TICKET_ID}?",
                "eval_ticket_lookup",
                hits=[],  # el KB no aporta contexto: respuesta = tool
                llm_answer=None,
            )
    assert "4bccd74e" in t4["final_state"]["answer"]
    assert t4["final_state"]["incident_result"]["resolved"] is True
    t4_order = [s["node"] for s in t4["steps"]]
    assert "retrieve" not in t4_order, f"ticket operacional debe ser tool-only: {t4_order}"

    # 5. Consulta de stock con la tool y el modelo del servicio reales.
    with _seeded_inventory_manager():
        t5 = _run(
            "¿Cuánto stock hay del producto CLT-SNK-W-42?",
            "eval_inventory_lookup",
            hits=[],  # el KB no aporta contexto: respuesta = tool
            llm_answer=None,
        )
    assert "CLT-SNK-W-42" in t5["final_state"]["answer"]
    assert "40" in t5["final_state"]["answer"]
    inv_result = t5["final_state"]["inventory_result"]
    assert inv_result is not None and inv_result["resolved"] is True
    t5_order = [s["node"] for s in t5["steps"]]
    assert "retrieve" not in t5_order, f"stock operacional debe ser tool-only: {t5_order}"

    # 6. Nuevo eval tool-only con ID propio: incidencia consultada en TinyDB
    # real; la base temporal hace reproducible la corrida.
    with tempfile.TemporaryDirectory() as tmp:
        with _seeded_incident_manager(Path(tmp)):
            reset_mcp_client()
            t6 = _run(
                f"¿Cuál es el estado del ticket {EVAL_TICKET_ID}?",
                "eval_routing_tool_only_ticket",
                hits=[],
                llm_answer=None,
            )
    assert "incident_lookup" in [s["node"] for s in t6["steps"]]
    assert "retrieve" not in [s["node"] for s in t6["steps"]]

    # 7. Nuevo eval RAG-only: documentación real y ninguna tool disparada.
    t7 = _run(
        "¿Cuál es la ventana estándar de devolución de TrackFlow?",
        "eval_routing_rag_only_returns",
        hits=[_returns_chunk()],
        llm_answer="TrackFlow ofrece una ventana estándar de devolución de 30 días naturales.",
    )
    t7_order = [s["node"] for s in t7["steps"]]
    assert "retrieve" in t7_order
    assert "incident_lookup" not in t7_order and "inventory_lookup" not in t7_order

    for tid in (
        "eval_policy_discount",
        "eval_empty_question",
        "eval_no_context",
        "eval_ticket_lookup",
        "eval_inventory_lookup",
        "eval_routing_tool_only_ticket",
        "eval_routing_rag_only_returns",
    ):
        path = tracing.TRACES_DIR / f"{tid}.json"
        steps = [s["node"] for s in json.loads(path.read_text())["steps"]]
        print(f"{tid}: {' -> '.join(steps)}  [{path}]")


if __name__ == "__main__":
    main()
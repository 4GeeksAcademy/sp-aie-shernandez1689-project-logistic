"""Genera los traces-fixture que consumen los evals del agente RAG.

Cada escenario ejecuta el grafo real (services.rag_agent.graph) con las
dependencias externas mockeadas (Qdrant y LLM), igual que tests/pipelines/
test_rag.py. El resultado es un trace JSON persistido en
data/eval/agent_traces/<thread_id>.json con el mismo schema que produciría
una corrida con servicios vivos.

Escenarios:
  1. eval_policy_discount — pregunta de política conocida; el chunk viene
     del company-knowledge-base real (trackflow-storage-pricing.es.md).
  2. eval_empty_question  — pregunta vacía -> rama error, sin retrieve.
  3. eval_no_context      — retrieve sin hits -> no_context, sin generate.
  4. eval_ticket_lookup   — pregunta de ticket -> tool incident_lookup (lee
     el gestor de incidencias real). Aunque la tool responde, retrieve corre
     SIEMPRE para comprobar el KB (sin hits aquí -> la respuesta final es el
     resumen de la tool, sin llamar al LLM).
  5. eval_inventory_lookup — pregunta de stock -> tool inventory_lookup.
     Igual que en 4: la tool responde y retrieve comprueba el KB después
     (sin hits -> respuesta = resumen de la tool). La tool se mockea igual
     que Qdrant/LLM (la base del gestor requiere DATABASE_URL, no disponible
     al generar traces).

Ejecución: uv run python tests/pipelines/_gen_agent_traces.py
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.rag_agent.graph import graph
from services.rag_agent import tracing
from services.rag_agent.tools.inventory_lookup import InventoryLookupResult

REPO_ROOT = Path(__file__).resolve().parents[2]
KB_DOC = REPO_ROOT / "docs" / "company-knowledge-base" / "trackflow-storage-pricing.es.md"

# Id de incidencia de evaluación: lost_parcel abierto, origen branch (sucursal
# la_warehouse) — el mismo que usa test_agent_graph_evals.py.
EVAL_TICKET_ID = "4bccd74e-f1ee-4883-8985-cf73ca332b8c"

# Producto real del seed de services/user-api (seed_inventory.py). La cifra
# coincide con current_stock = Σentradas − Σsalidas que expone la API.
EVAL_INVENTORY_PRODUCT = {
    "id": 1,
    "name": "Zapatilla blanca clásica - Talla 42",
    "sku": "CLT-SNK-W-42",
    "client_name": "PureStep Footwear",
    "category": "fashion",
    "warehouse": "LA",
    "current_stock": 40,
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
            from services.rag_agent.tools.incident_lookup import reset_reader

            reset_reader()  # el reader perezoso usa DB_PATH actual
            t4 = _run(
                f"¿cuál es el estado del ticket {EVAL_TICKET_ID}?",
                "eval_ticket_lookup",
                hits=[],  # el KB no aporta contexto: respuesta = tool
                llm_answer=None,
            )
    assert "4bccd74e" in t4["final_state"]["answer"]
    assert t4["final_state"]["incident_result"]["resolved"] is True
    t4_order = [s["node"] for s in t4["steps"]]
    assert "retrieve" in t4_order, f"retrieve debe correr tras la tool: {t4_order}"

    # 5. Consulta de stock: la tool inventory_lookup responde y retrieve
    #    comprueba el KB después (hits=[] -> la respuesta final es el resumen
    #    de la tool, sin llamar al LLM). Se mockea lookup_inventory igual que
    #    se mockea Qdrant/LLM: el gestor real requiere DATABASE_URL (no
    #    configurada al generar traces). El resultado reproduce el SKUResponse
    #    real del seed.
    fake_inventory = InventoryLookupResult(product=EVAL_INVENTORY_PRODUCT, resolved=True)
    with patch(
        "services.rag_agent.graph.lookup_inventory",
        return_value=fake_inventory,
    ):
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
    assert "retrieve" in t5_order, f"retrieve debe correr tras la tool: {t5_order}"

    for tid in (
        "eval_policy_discount",
        "eval_empty_question",
        "eval_no_context",
        "eval_ticket_lookup",
        "eval_inventory_lookup",
    ):
        path = tracing.TRACES_DIR / f"{tid}.json"
        steps = [s["node"] for s in json.loads(path.read_text())["steps"]]
        print(f"{tid}: {' -> '.join(steps)}  [{path}]")


if __name__ == "__main__":
    main()
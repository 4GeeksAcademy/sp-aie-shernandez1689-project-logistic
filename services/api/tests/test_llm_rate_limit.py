"""Autenticación y rate limiting de las rutas que invocan un modelo (informe NIST, PR-6).

``POST /agent/query``, ``POST /api/rag/query`` y ``POST /knowledge/query``
exigen JWT y comparten un presupuesto de llamadas al LLM por usuario. El LLM,
Qdrant y el grafo se sustituyen por stubs: no hay llamadas externas.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.agent as agent_routes  # noqa: E402
import routes.knowledge as knowledge_routes  # noqa: E402
import routes.rag as rag_routes  # noqa: E402
from auth import CurrentUser, get_current_user  # noqa: E402
from main import app  # noqa: E402
from rate_limit import SlidingWindowLimiter, llm_limiter  # noqa: E402

MODEL_ROUTES = {
    "/agent/query": {"question": "¿Cuál es la política de devoluciones?"},
    "/api/rag/query": {"question": "¿Cuál es la política de devoluciones?"},
    "/knowledge/query": {"question": "¿Cuál es la política de devoluciones?"},
}


class FakeGraph:
    def __init__(self):
        self.configs = []

    def invoke(self, state, config):
        self.configs.append(config)
        return {"answer": "Devoluciones en 30 días.", "context": []}


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph()
    monkeypatch.setattr(agent_routes, "graph", fake)
    monkeypatch.setattr(agent_routes, "load_trace", lambda thread_id: {"steps": [{"node": "generate"}]})
    monkeypatch.setattr(rag_routes, "retrieve", lambda **_: [])
    monkeypatch.setattr(rag_routes, "generate_answer", lambda **_: "Devoluciones en 30 días.")
    monkeypatch.setattr(knowledge_routes, "rag_query", lambda **_: "Devoluciones en 30 días.")
    return fake


@pytest.fixture
def client(graph):
    llm_limiter.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()
    llm_limiter.reset()


def _login_as(user_id: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, email=f"{user_id}@trackflow.example", role="user",
    )


@pytest.mark.parametrize("path", MODEL_ROUTES)
def test_model_routes_require_jwt(client, graph, path):
    resp = client.post(path, json=MODEL_ROUTES[path])

    assert resp.status_code == 401
    assert graph.configs == []


@pytest.mark.parametrize("path", MODEL_ROUTES)
def test_model_routes_answer_with_jwt(client, path):
    _login_as("usr_a")

    assert client.post(path, json=MODEL_ROUTES[path]).status_code == 200, path


def test_budget_is_shared_across_model_routes_and_returns_429(client, graph, monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_PER_MINUTE", "2")
    _login_as("usr_a")

    assert client.post("/agent/query", json=MODEL_ROUTES["/agent/query"]).status_code == 200
    assert client.post("/api/rag/query", json=MODEL_ROUTES["/api/rag/query"]).status_code == 200
    blocked = client.post("/knowledge/query", json=MODEL_ROUTES["/knowledge/query"])

    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert "Límite de consultas al modelo" in blocked.json()["detail"]
    # El rechazo ocurre antes de llamar al modelo.
    assert client.post("/agent/query", json=MODEL_ROUTES["/agent/query"]).status_code == 429
    assert len(graph.configs) == 1

    # Otro usuario tiene su propio presupuesto.
    _login_as("usr_b")
    assert client.post("/agent/query", json=MODEL_ROUTES["/agent/query"]).status_code == 200


def test_agent_query_runs_with_the_callers_user_id(client, graph):
    _login_as("usr_a")

    client.post("/agent/query", json=MODEL_ROUTES["/agent/query"])

    assert graph.configs[0]["configurable"]["user_id"] == "usr_a"


def test_window_slides_and_frees_the_budget():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=lambda: 2, window=60.0, clock=lambda: now[0])

    assert limiter.hit("u") is None
    now[0] = 30.0
    assert limiter.hit("u") is None
    assert limiter.hit("u") == pytest.approx(30.0)  # la primera caduca en t=60
    now[0] = 60.0
    assert limiter.hit("u") is None
    assert limiter.hit("other") is None

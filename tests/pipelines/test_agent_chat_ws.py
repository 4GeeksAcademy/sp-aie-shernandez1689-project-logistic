"""Chat en tiempo real con ``first_line_cx`` (``WS /agent/ws``).

Extiende ``POST /agent/query`` con streaming token a token y control de
operador (company-brifing2.md). Se prueba por capas:

- ``rag``: ``generate_answer(on_token=...)`` pide ``stream`` al LLM, entrega
  cada delta y devuelve el texto completo; sin ``on_token`` la llamada es la
  de siempre. ``StreamAborted`` desde el callback no se traga.
- ``graph``: ``generate_node`` emite tokens por el modo ``custom`` solo si
  ``configurable.stream_tokens``; ``graph.invoke`` (POST /agent/query) no.
- Bus pub/sub: tópicos por ``flow_id`` desacoplados de los sockets.
- WebSocket (contrato ``{"event", "data"}`` con ``agent_id``/``flow_id``):
  handshake con JWT + sesión existente antes de cualquier evento,
  ``token_chunk`` → ``generation_completed``; interrupción a mitad de
  respuesta (``generation_interrupted``: ni un token más de esa generación,
  parcial conservado como ``interrupted``, turno nuevo con ``new_input``);
  reconexión con el mismo ``flow_id`` que rehidrata el hilo (también con la
  generación aún en curso); control de operador pause/resume/cancel.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "services" / "api"
for _path in (REPO_ROOT, API_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import agent_chat  # noqa: E402
import auth  # noqa: E402
import data.pipelines.rag as rag  # noqa: E402
import rate_limit  # noqa: E402
import services.rag_agent.graph as graph_mod  # noqa: E402
from data.pipelines.rag import StreamAborted  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routes import agent as agent_route  # noqa: E402
from services.rag_agent import tracing  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

JWT_SECRET = "test-secret-agent-chat-websocket-0123456789"
USERS = {
    "user_0207": {"id": "user_0207", "email": "eng@trackflow.test", "role": "engineering"},
    "user_0311": {"id": "user_0311", "email": "ops@trackflow.test", "role": "operations"},
    "user_0400": {"id": "user_0400", "email": "cx@trackflow.test", "role": "user"},
    "user_0401": {"id": "user_0401", "email": "cx2@trackflow.test", "role": "user"},
}
CHAT_IDS = {"agent_id": "first_line_cx"}


# ── rag: streaming del LLM ────────────────────────────────────────────────


def _sse_body(tokens: list[str]) -> bytes:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": tok}}]})
        for tok in tokens
    ]
    lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


@pytest.fixture
def llm_requests(monkeypatch):
    """Sustituye el transporte HTTP del LLM; devuelve los JSON enviados."""
    sent: list[dict[str, Any]] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse_body(["El SLA ", "es de ", "48 h."]),
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "El SLA es de 48 h."}}]}
        )

    monkeypatch.setattr(
        rag.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return sent


CONTEXT = [{"text": "SLA estándar 48 h", "source_document": "sla.md", "section": "SLA"}]


def test_generate_answer_streams_tokens_and_returns_full_text(llm_requests):
    tokens: list[str] = []
    answer = rag.generate_answer("¿SLA?", CONTEXT, on_token=tokens.append)
    assert tokens == ["El SLA ", "es de ", "48 h."]
    assert answer == "El SLA es de 48 h."
    assert llm_requests[0]["stream"] is True


def test_generate_answer_without_callback_keeps_plain_request(llm_requests):
    assert rag.generate_answer("¿SLA?", CONTEXT) == "El SLA es de 48 h."
    assert "stream" not in llm_requests[0]


def test_casual_answer_streams_tokens(llm_requests):
    tokens: list[str] = []
    assert rag.generate_casual_answer("¿hora?", on_token=tokens.append) == "El SLA es de 48 h."
    assert len(tokens) == 3


def test_stream_aborted_from_callback_propagates(llm_requests):
    def on_token(_token: str) -> None:
        raise StreamAborted("cancelled")

    with pytest.raises(StreamAborted):
        rag.generate_answer("¿SLA?", CONTEXT, on_token=on_token)


# ── graph: modo custom solo cuando se pide ────────────────────────────────


@pytest.fixture
def generate_only_graph(tmp_path, monkeypatch):
    """El ``generate_node`` real en un grafo mínimo con checkpointer SQLite.

    El trace es global (``tracing._active``): otro test que falle a mitad de
    corrida puede dejarlo abierto, así que se aísla aquí.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    monkeypatch.setattr(tracing, "_active", None)
    monkeypatch.setattr(tracing, "TRACES_DIR", tmp_path)
    from langgraph.graph import END, START, StateGraph

    calls: list[Any] = []

    def fake_generate_answer(question, context, *, on_token=None):
        calls.append(on_token)
        if on_token is not None:
            for tok in ["Devoluciones ", "en 30 días."]:
                on_token(tok)
        return "Devoluciones en 30 días."

    builder = StateGraph(graph_mod.AgentState)
    builder.add_node("generate", graph_mod.generate_node)
    builder.add_edge(START, "generate")
    builder.add_edge("generate", END)
    saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    with patch.object(graph_mod, "generate_answer", fake_generate_answer):
        yield builder.compile(checkpointer=saver), calls


STATE = {"question": "¿plazo de devoluciones?", "context": CONTEXT}


def test_generate_node_streams_tokens_in_custom_mode(generate_only_graph):
    graph, _calls = generate_only_graph
    gate_calls: list[int] = []
    config = {
        "configurable": {
            "thread_id": "chat_test1",
            "stream_tokens": True,
            "token_gate": lambda: gate_calls.append(1),
        }
    }
    chunks = list(graph.stream(STATE, config, stream_mode=["custom", "updates"]))
    assert chunks[:2] == [
        ("custom", {"token": "Devoluciones "}),
        ("custom", {"token": "en 30 días."}),
    ]
    assert chunks[-1] == ("updates", {"generate": {"answer": "Devoluciones en 30 días."}})
    assert len(gate_calls) == 2  # la compuerta se consulta antes de cada token


def test_graph_invoke_does_not_stream(generate_only_graph):
    """POST /agent/query sigue con la llamada al LLM sin streaming."""
    graph, calls = generate_only_graph
    state = graph.invoke(STATE, {"configurable": {"thread_id": "chat_test2"}})
    assert state["answer"] == "Devoluciones en 30 días."
    assert calls == [None]


def test_abort_run_closes_dangling_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "TRACES_DIR", tmp_path)
    tracing.start_run("chat_trace1", "pregunta")
    tracing.abort_run("other_thread", "cancelled")  # no toca otro trace
    assert tracing._active is not None
    tracing.abort_run("chat_trace1", "cancelled")
    assert tracing._active is None
    assert tracing.load_trace("chat_trace1")["final_state"] == {"aborted": "cancelled"}


# ── Bus pub/sub ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bus_fans_out_per_flow_and_signals_lagging_consumer():
    bus = agent_chat.ChatEventBus(queue_size=2)
    a, b = bus.subscribe("chat_a"), bus.subscribe("chat_a")
    other = bus.subscribe("chat_b")
    bus.publish("chat_a", "token_chunk", {"token": "x"})
    assert a.queue.get_nowait() == b.queue.get_nowait() == {"event": "token_chunk", "data": {"token": "x"}}
    assert other.queue.empty()

    bus.unsubscribe(b)
    assert bus.subscriber_count("chat_a") == 1
    for _ in range(3):  # desborda la cola de ``a``
        bus.publish("chat_a", "token_chunk", {"token": "y"})
    assert a.lagged and a.queue.get_nowait() is None  # → cerrar y rehidratar


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── WebSocket ─────────────────────────────────────────────────────────────


class FakeGraph:
    """Doble del grafo compilado: misma firma de ``stream`` y misma compuerta.

    Responde ``["Respuesta ", "a ", <pregunta>]``. Con ``hold=True`` la
    PRIMERA corrida espera a ``proceed`` antes del segundo token, para que el
    test actúe justo a mitad de respuesta; con ``fail=True`` la primera
    corrida falla tras el primer nodo.
    """

    def __init__(self, hold: bool = False, fail: bool = False) -> None:
        self.hold = hold
        self.fail = fail
        self.proceed = threading.Event()
        self.emitted: list[str] = []
        self.aborted = threading.Event()
        self.questions: list[str] = []
        self.configs: list[dict] = []
        self.modes: list[list[str]] = []

    def stream(self, state, config, stream_mode):
        run = len(self.questions)
        self.questions.append(state["question"])
        self.configs.append(config)
        self.modes.append(stream_mode)
        gate = config["configurable"]["token_gate"]
        yield ("updates", {"validate": {"question": state["question"]}})
        if self.fail and run == 0:
            raise RuntimeError("qdrant caído")
        tokens = ["Respuesta ", "a ", state["question"]]
        try:
            for index, token in enumerate(tokens):
                if index == 1 and self.hold and run == 0:
                    assert self.proceed.wait(5)
                gate()
                self.emitted.append(token)
                yield ("custom", {"token": token})
        except StreamAborted:
            self.aborted.set()
            raise
        yield ("updates", {"generate": {"answer": "".join(tokens)}})
        yield ("updates", {"finalize": None})


@pytest.fixture
def client(monkeypatch):
    rate_limit.llm_limiter.reset()  # presupuesto de LLM por usuario: limpio en cada test
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setattr(auth, "_find_user", lambda user_id: USERS.get(user_id))
    app = FastAPI()
    app.include_router(agent_route.router)
    with TestClient(app) as test_client:
        yield test_client


def _use_graph(monkeypatch, fake: FakeGraph) -> FakeGraph:
    monkeypatch.setattr(agent_route, "graph", fake)
    return fake


@pytest.fixture
def fake_graph(monkeypatch):
    return _use_graph(monkeypatch, FakeGraph())


def _token(user_id: str) -> str:
    import jwt

    return jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600}, JWT_SECRET, algorithm="HS256"
    )


def _new_session(client, user_id: str = "user_0400") -> str:
    resp = client.post("/agent/sessions", headers={"Authorization": f"Bearer {_token(user_id)}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["agent_id"] == "first_line_cx" and body["flow_type"] == "chat_session"
    assert body["flow_id"].startswith("chat_")
    return body["flow_id"]


def _ws(client, flow_id: str, user_id: str = "user_0400"):
    return client.websocket_connect(f"/agent/ws?token={_token(user_id)}&flow_id={flow_id}")


def _receive_until(ws, event: str, **match: Any) -> list[dict]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["event"] == event and all(frame["data"].get(k) == v for k, v in match.items()):
            return frames


def _restored(ws, flow_id: str) -> dict:
    frame = ws.receive_json()
    assert frame["event"] == "session_restored"
    assert frame["data"]["agent_id"] == "first_line_cx"
    assert frame["data"]["flow_id"] == flow_id
    return frame["data"]


def _say(ws, text: str) -> None:
    ws.send_json({"command": "send_message", "data": {"text": text}})


def test_create_session_requires_jwt(client):
    assert client.post("/agent/sessions").status_code == 401


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("flow_id=__flow__", 4401),  # sin token
        ("token=not-a-jwt&flow_id=__flow__", 4401),
        ("token=__token__", 4400),  # sin flow_id
        ("token=__token__&flow_id=chat_missing", 4404),
        ("token=__other__&flow_id=__flow__", 4403),  # sesión de otro usuario
    ],
)
def test_handshake_rejects_before_any_chat_event(client, fake_graph, query, code):
    flow_id = _new_session(client)
    query = (
        query.replace("__flow__", flow_id)
        .replace("__token__", _token("user_0400"))
        .replace("__other__", _token("user_0401"))
    )
    with client.websocket_connect(f"/agent/ws?{query}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()  # lo primero que llega es el cierre, no un evento
    assert exc.value.code == code
    assert fake_graph.questions == []


def test_event_contract_token_chunks_then_generation_completed(client, fake_graph):
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        restored = _restored(ws, flow_id)
        assert restored["messages"] == [] and restored["generation"] is None
        assert restored["flow_type"] == "chat_session"
        _say(ws, "¿Dónde está mi envío?")
        frames = _receive_until(ws, "generation_completed")

    events = [f["event"] for f in frames]
    assert events[:2] == ["message_created", "generation_started"]
    user_message = frames[0]["data"]["message"]
    assert user_message["role"] == "user" and user_message["status"] == "complete"
    assert user_message["content"] == "¿Dónde está mi envío?"
    message_id = frames[1]["data"]["message_id"]
    assert frames[1]["data"]["reply_to"] == user_message["message_id"]

    chunks = [f["data"] for f in frames if f["event"] == "token_chunk"]
    assert chunks == [
        {"agent_id": "first_line_cx", "flow_id": flow_id, "message_id": message_id, "token": t}
        for t in ["Respuesta ", "a ", "¿Dónde está mi envío?"]
    ]
    assert [f["data"]["node"] for f in frames if f["event"] == "generation_step"] == [
        "validate", "generate", "finalize",
    ]
    assert frames[-1]["data"] == {
        "agent_id": "first_line_cx",
        "flow_id": flow_id,
        "message_id": message_id,
        "content": "Respuesta a ¿Dónde está mi envío?",
    }
    config = fake_graph.configs[0]["configurable"]
    assert config["thread_id"] == flow_id and config["stream_tokens"] is True
    assert fake_graph.modes[0] == ["custom", "updates"]


def test_new_input_mid_response_interrupts_and_starts_new_turn(client, monkeypatch):
    fake = _use_graph(monkeypatch, FakeGraph(hold=True))
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "estado del envío TF-1")
        first = _receive_until(ws, "token_chunk")
        old_id = first[1]["data"]["message_id"]

        _say(ws, "mejor dime el plazo de devolución")  # nuevo input a mitad de respuesta
        interrupted = _receive_until(ws, "generation_interrupted")[-1]["data"]
        assert interrupted["message_id"] == old_id
        assert interrupted["reason"] == "new_input"
        assert interrupted["content"] == "Respuesta "  # parcial ya mostrado, intacto

        fake.proceed.set()  # la generación original intenta seguir
        after = _receive_until(ws, "generation_completed")

    assert fake.aborted.wait(2)
    assert fake.emitted.count("a ") == 1  # solo el del turno nuevo
    assert not [
        f for f in after if f["event"] == "token_chunk" and f["data"]["message_id"] == old_id
    ]
    created = next(f for f in after if f["event"] == "message_created")["data"]["message"]
    started = next(f for f in after if f["event"] == "generation_started")["data"]
    assert created["content"] == "mejor dime el plazo de devolución"
    assert started["reply_to"] == created["message_id"]
    assert started["message_id"] != old_id
    assert after[-1]["data"]["content"] == "Respuesta a mejor dime el plazo de devolución"
    assert fake.questions == ["estado del envío TF-1", "mejor dime el plazo de devolución"]

    with _ws(client, flow_id) as ws:  # el historial conserva el parcial interrumpido
        history = _restored(ws, flow_id)["messages"]
    assert [(m["role"], m["status"], m["content"]) for m in history] == [
        ("user", "complete", "estado del envío TF-1"),
        ("assistant", "interrupted", "Respuesta "),
        ("user", "complete", "mejor dime el plazo de devolución"),
        ("assistant", "complete", "Respuesta a mejor dime el plazo de devolución"),
    ]


def test_interrupt_command_without_new_input(client, monkeypatch):
    fake = _use_graph(monkeypatch, FakeGraph(hold=True))
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "estado")
        _receive_until(ws, "token_chunk")
        ws.send_json({"command": "interrupt", "data": {}})
        frame = _receive_until(ws, "generation_interrupted")[-1]
        assert frame["data"]["reason"] == "user_interrupt"
        fake.proceed.set()
        assert fake.aborted.wait(2)

        ws.send_json({"command": "interrupt", "data": {}})
        assert ws.receive_json()["data"]["code"] == "invalid_state"


def test_send_message_over_llm_rate_limit_reports_chat_error(client, fake_graph, monkeypatch):
    """PR-6 (informe NIST): el WS comparte el presupuesto de llamadas al LLM por usuario."""
    monkeypatch.setenv("LLM_RATE_LIMIT_PER_MINUTE", "1")
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "¿Dónde está mi envío?")
        _receive_until(ws, "generation_completed")
        _say(ws, "¿Y el de ayer?")
        error = _receive_until(ws, "chat_error")[-1]["data"]

    assert error["code"] == "rate_limited"
    assert "Reintenta en" in error["message"]
    assert fake_graph.questions == ["¿Dónde está mi envío?"]


def test_reconnect_with_same_flow_id_restores_thread(client, fake_graph):
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "plazo de devolución")
        _receive_until(ws, "generation_completed")

    with _ws(client, flow_id) as ws:  # reconexión: mismo hilo, no un chat vacío
        restored = _restored(ws, flow_id)
        assert [m["content"] for m in restored["messages"]] == [
            "plazo de devolución", "Respuesta a plazo de devolución",
        ]
        _say(ws, "¿y para Zaragoza?")
        _receive_until(ws, "generation_completed")
    assert {c["configurable"]["thread_id"] for c in fake_graph.configs} == {flow_id}


def test_generation_survives_disconnect_and_resumes_on_reconnect(client, monkeypatch):
    """Pub/sub: el productor no depende del socket; al volver se rehidrata."""
    fake = _use_graph(monkeypatch, FakeGraph(hold=True))
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "estado")
        message_id = _receive_until(ws, "token_chunk")[-1]["data"]["message_id"]
    # Caída de red a mitad de respuesta.

    with _ws(client, flow_id) as ws:
        restored = _restored(ws, flow_id)
        assert restored["generation"] == {"message_id": message_id, "paused": False}
        partial = restored["messages"][-1]
        assert (partial["status"], partial["content"]) == ("streaming", "Respuesta ")

        fake.proceed.set()
        frames = _receive_until(ws, "generation_completed")
    tokens = [f["data"]["token"] for f in frames if f["event"] == "token_chunk"]
    assert tokens == ["a ", "estado"]  # solo lo nuevo, sin duplicar el parcial
    assert frames[-1]["data"]["content"] == "Respuesta a estado"
    assert not fake.aborted.is_set()


def test_operator_pauses_and_resumes_another_session(client, monkeypatch):
    fake = _use_graph(monkeypatch, FakeGraph(hold=True))
    customer_flow = _new_session(client, "user_0400")
    operator_flow = _new_session(client, "user_0207")
    target = {**CHAT_IDS, "flow_id": customer_flow}
    with _ws(client, customer_flow) as customer:
        _restored(customer, customer_flow)
        _say(customer, "estado")
        _receive_until(customer, "token_chunk")

        with _ws(client, operator_flow, "user_0207") as operator:
            _restored(operator, operator_flow)
            operator.send_json({"command": "pause", "data": target})
            by_operator = operator.receive_json()
            by_customer = _receive_until(customer, "control_applied")[-1]
            assert by_operator == by_customer
            assert by_customer["data"]["action"] == "pause"
            assert by_customer["data"]["actor_id"] == "user_0207"
            assert by_customer["data"]["timestamp"].endswith("Z")

            fake.proceed.set()
            time.sleep(0.3)
            assert fake.emitted == ["Respuesta "]  # detenida a mitad de stream

            operator.send_json({"command": "resume", "data": target})
            assert operator.receive_json()["data"]["action"] == "resume"
        frames = _receive_until(customer, "generation_completed")
    assert [f["data"]["token"] for f in frames if f["event"] == "token_chunk"] == ["a ", "estado"]
    assert len(fake.questions) == 1  # resume no repite nada


def test_supervisor_observes_same_events_without_extra_agent_calls(client, fake_graph):
    """Varios suscriptores de la misma sesión: mismos frames, una sola corrida."""
    flow_id = _new_session(client, "user_0400")
    with _ws(client, flow_id) as customer, _ws(client, flow_id, "user_0207") as supervisor:
        assert _restored(customer, flow_id) == _restored(supervisor, flow_id)
        _say(customer, "estado del envío")
        seen_by_customer = _receive_until(customer, "generation_completed")
        seen_by_supervisor = _receive_until(supervisor, "generation_completed")
        assert seen_by_customer == seen_by_supervisor
        assert fake_graph.questions == ["estado del envío"]  # una llamada, no dos

        # El supervisor no escribe como el cliente.
        _say(supervisor, "mensaje del supervisor")
        assert supervisor.receive_json()["data"]["code"] == "read_only"
        supervisor.send_json({"command": "interrupt", "data": {}})
        assert supervisor.receive_json()["data"]["code"] == "read_only"
    assert fake_graph.questions == ["estado del envío"]


def test_operator_cancel_closes_session(client, monkeypatch):
    fake = _use_graph(monkeypatch, FakeGraph(hold=True))
    flow_id = _new_session(client, "user_0311")
    with _ws(client, flow_id, "user_0311") as ws:
        _restored(ws, flow_id)
        _say(ws, "estado")
        _receive_until(ws, "token_chunk")
        ws.send_json({"command": "cancel", "data": {**CHAT_IDS, "flow_id": flow_id}})
        frames = _receive_until(ws, "chat_session_closed")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1000
    events = [f["event"] for f in frames]
    assert events[-3:] == ["generation_interrupted", "control_applied", "chat_session_closed"]
    assert frames[-3]["data"]["reason"] == "cancelled"
    assert frames[-2]["data"] == {
        "agent_id": "first_line_cx",
        "flow_id": flow_id,
        "action": "cancel",
        "actor_id": "user_0311",
        "timestamp": frames[-2]["data"]["timestamp"],
    }
    fake.proceed.set()
    assert fake.aborted.wait(2)

    with _ws(client, flow_id, "user_0311") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4410


def test_control_requires_engineering_or_operations_role(client, fake_graph):
    flow_id = _new_session(client, "user_0400")
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        ws.send_json({"command": "cancel", "data": {**CHAT_IDS, "flow_id": flow_id}})
        assert ws.receive_json() == {
            "event": "chat_error",
            "data": {
                "code": "forbidden",
                "message": "Se requiere un usuario del backoffice con rol de ingeniería u operaciones.",
            },
        }


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ("no-json", "invalid_command"),
        ({"command": "approve", "data": {}}, "invalid_command"),
        ({"command": "send_message", "data": {"text": "   "}}, "invalid_message"),
        ({"command": "send_message", "data": {"text": "x" * 1001}}, "invalid_message"),
        ({"command": "interrupt", "data": {}}, "invalid_state"),
        ({"command": "pause", "data": {"flow_id": "chat_x"}}, "invalid_command"),
        ({"command": "pause", "data": {"agent_id": "rfp_pipeline", "flow_id": "flow_0509"}},
         "unsupported_agent"),
        ({"command": "pause", "data": {**CHAT_IDS, "flow_id": "chat_missing"}}, "flow_not_found"),
        ({"command": "resume", "data": {**CHAT_IDS, "flow_id": "__self__"}}, "invalid_state"),
        ({"command": "pause", "data": {**CHAT_IDS, "flow_id": "__self__"}}, "invalid_state"),
    ],
)
def test_invalid_commands_report_chat_error(client, fake_graph, payload, code):
    flow_id = _new_session(client, "user_0207")
    with _ws(client, flow_id, "user_0207") as ws:
        _restored(ws, flow_id)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        ws.send_text(text.replace("__self__", flow_id))
        frame = ws.receive_json()
        _say(ws, "hola")  # la sesión sigue abierta tras el error
        _receive_until(ws, "generation_completed")
    assert frame["event"] == "chat_error"
    assert frame["data"]["code"] == code


def test_graph_failure_marks_message_failed_and_keeps_session(client, monkeypatch):
    _use_graph(monkeypatch, FakeGraph(fail=True))
    flow_id = _new_session(client)
    with _ws(client, flow_id) as ws:
        _restored(ws, flow_id)
        _say(ws, "estado")
        failed = _receive_until(ws, "generation_failed")[-1]["data"]
        assert "qdrant" not in failed["message"]  # sin detalles internos

        _say(ws, "estado")
        _receive_until(ws, "generation_completed")
    with _ws(client, flow_id) as ws:
        statuses = [m["status"] for m in _restored(ws, flow_id)["messages"]]
    assert statuses == ["complete", "failed", "complete", "complete"]

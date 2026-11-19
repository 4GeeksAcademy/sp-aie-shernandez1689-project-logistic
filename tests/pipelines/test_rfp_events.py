"""Notificaciones SSE de tickets RFP (``GET /rfp/events``).

Sustituyen el polling de ``uis/backoffice/rfp``. Se prueba por capas:

- ``db``: el pipeline avisa a sus observadores en cada alta/cambio del ticket
  (``created=True`` solo en el alta, que llega en ``analizando``).
- ``rfp_events``: el broker serializa el Ticket del BRIEFING §2.3 como frame
  SSE nombrado, lo entrega entre hilos y reproduce lo perdido con
  ``Last-Event-ID`` (o pide ``rfp_resync`` si ya no puede).
- HTTP real (uvicorn, no TestClient: el stream no termina): headers
  ``text/event-stream``, JWT obligatorio, frames ``event: rfp_ticket_created``
  con ``data:`` JSON, y reconexión con ``Last-Event-ID`` que recupera los
  tickets llegados durante la caída una sola vez.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "services" / "api"
for _path in (REPO_ROOT, API_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import rfp_events  # noqa: E402
from rfp_events import (  # noqa: E402
    METADATA_EVENT_FIELDS,
    TICKET_EVENT_FIELDS,
    TicketEventBroker,
    format_sse,
)

SAMPLE_PDF = REPO_ROOT / "rfp-requests" / "trackflow" / "trackflow-request-1.pdf"
# Ticket del BRIEFING §2.3.
TICKET_FIELDS = {"ticket_id", "rfp_id", "status", "raw_pdf_path", "created_at", "updated_at"}
# Metadatos de la RFP del BRIEFING §2.3 que viajan en el evento.
RFP_METADATA_FIELDS = {
    "client_name", "client_country", "services_requested", "monthly_volume",
    "deadline", "budget_range", "departments_needed",
}
FINAL_STATUS = "esperando_aprobación"  # con RFP_AUTO_APPROVAL=0


@pytest.fixture
def sqlite_db():
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import data.pipelines.rfp_intake.db as db_mod

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    ).execution_options(schema_translate_map={"rfp_intake": None})
    db_mod.create_tables(engine)
    db_mod.set_engine(engine)
    yield db_mod
    db_mod.set_engine(None)


@pytest.fixture
def captured(sqlite_db):
    events: list[tuple[dict, bool]] = []

    def listener(ticket, created):
        events.append((ticket, created))

    sqlite_db.add_ticket_listener(listener)
    yield events
    sqlite_db.remove_ticket_listener(listener)


def _ticket(ticket_id: str = "rfp_t1", status: str = "analizando") -> dict:
    return {
        "ticket_id": ticket_id,
        "rfp_id": f"{ticket_id}-rfp",
        "status": status,
        "raw_pdf_path": f"data/raw/rfp/{ticket_id}.pdf",
        "reason": "análisis en curso",
    }


def _parse(frame: str) -> dict:
    fields: dict = {}
    for line in frame.strip().splitlines():
        key, _, value = line.partition(": ")
        fields[key] = value
    fields["data"] = json.loads(fields["data"])
    return fields


# ===================================================================
# Pipeline: observadores del ticket
# ===================================================================


class TestTicketListeners:
    def test_new_ticket_notifies_created_in_initial_status(self, sqlite_db, captured):
        sqlite_db.save_ticket(_ticket())

        [(ticket, created)] = captured
        assert created is True
        assert ticket["status"] == "analizando"
        assert TICKET_FIELDS <= set(ticket)

    def test_status_changes_notify_updates(self, sqlite_db, captured):
        sqlite_db.save_ticket(_ticket())
        sqlite_db.update_ticket("rfp_t1", status="generando_borrador", reason="Parte 2")
        sqlite_db.save_ticket(_ticket(status="analizando"))  # reanálisis

        assert [(t["status"], c) for t, c in captured] == [
            ("analizando", True),
            ("generando_borrador", False),
            ("analizando", False),
        ]
        assert captured[1][0]["reason"] == "Parte 2"

    def test_save_intake_notifies_once_with_final_status(self, sqlite_db, captured):
        sqlite_db.save_ticket(_ticket())
        sqlite_db.save_intake(_ticket(status="descartado"), None, [])

        assert [(t["status"], c) for t, c in captured] == [("analizando", True), ("descartado", False)]

    def test_failing_listener_never_breaks_the_pipeline(self, sqlite_db, captured):
        def broken(ticket, created):
            raise RuntimeError("boom")

        sqlite_db.add_ticket_listener(broken)
        try:
            sqlite_db.save_ticket(_ticket())
        finally:
            sqlite_db.remove_ticket_listener(broken)

        assert sqlite_db.get_ticket("rfp_t1")["status"] == "analizando"
        assert len(captured) == 1

    def test_add_is_idempotent_and_remove_stops_events(self, sqlite_db):
        events = []
        listener = lambda ticket, created: events.append(created)  # noqa: E731
        sqlite_db.add_ticket_listener(listener)
        sqlite_db.add_ticket_listener(listener)
        sqlite_db.save_ticket(_ticket())
        sqlite_db.remove_ticket_listener(listener)
        sqlite_db.update_ticket("rfp_t1", status="descartado")

        assert events == [True]

    def test_api_registers_the_sse_publisher(self):
        import data.pipelines.rfp_intake.db as db_mod
        import routes.rfp

        assert routes.rfp._publish_ticket_event in db_mod._TICKET_LISTENERS


# ===================================================================
# Broker SSE
# ===================================================================


class TestBroker:
    def test_format_sse_frame(self):
        frame = format_sse("rfp_ticket_created", {"status": "analizando"}, "e1-7")
        assert frame == 'id: e1-7\nevent: rfp_ticket_created\ndata: {"status": "analizando"}\n\n'

    def test_payload_is_the_briefing_ticket_plus_rfp_metadata(self):
        async def scenario():
            broker = TicketEventBroker()
            queue, missed = broker.subscribe()
            ticket = {**_ticket(), "attempts": 1, "created_at": "x", "updated_at": "y",
                      "client_name": "ModaViva", "doc_type": "rfp"}
            broker.publish_ticket(ticket, True)
            return broker, missed, await asyncio.wait_for(queue.get(), 1)

        broker, missed, raw = asyncio.run(scenario())
        frame = _parse(raw)
        assert missed == []
        assert frame["event"] == "rfp_ticket_created"
        assert frame["id"] == f"{broker.epoch}-1"
        # Sin ``attempts`` (interno) ni campos fuera del contrato.
        assert set(frame["data"]) == set(TICKET_EVENT_FIELDS) | set(METADATA_EVENT_FIELDS)
        assert frame["data"]["status"] == "analizando"
        assert frame["data"]["client_name"] == "ModaViva"

    def test_publish_from_worker_thread_reaches_every_subscriber(self):
        async def scenario():
            broker = TicketEventBroker()
            queues = [broker.subscribe()[0], broker.subscribe()[0]]
            worker = threading.Thread(
                target=broker.publish_ticket, args=(_ticket(status="terminado"), False)
            )
            worker.start()
            worker.join()
            return [await asyncio.wait_for(q.get(), 1) for q in queues]

        frames = [_parse(f) for f in asyncio.run(scenario())]
        assert [f["event"] for f in frames] == ["rfp_ticket_updated", "rfp_ticket_updated"]
        assert {f["data"]["status"] for f in frames} == {"terminado"}

    def test_slow_subscriber_keeps_latest_events(self, monkeypatch):
        monkeypatch.setattr(rfp_events, "SUBSCRIBER_QUEUE_SIZE", 2)

        async def scenario():
            broker = TicketEventBroker()
            queue, _ = broker.subscribe()
            for status in ("analizando", "analisis_completo", "generando_borrador"):
                broker.publish_ticket(_ticket(status=status), False)
            await asyncio.sleep(0)
            return [queue.get_nowait() for _ in range(queue.qsize())]

        statuses = [_parse(f)["data"]["status"] for f in asyncio.run(scenario())]
        assert statuses == ["analisis_completo", "generando_borrador"]

    def test_stream_heartbeat_events_and_unsubscribe_on_disconnect(self):
        async def scenario():
            broker = TicketEventBroker()
            disconnected = False

            async def is_disconnected():
                return disconnected

            stream = broker.stream(is_disconnected, heartbeat=0.01)
            chunks = [await anext(stream), await anext(stream), await anext(stream)]
            assert broker.subscriber_count == 1
            broker.publish_ticket(_ticket(), True)
            chunks.append(await anext(stream))
            disconnected = True
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
            return chunks, broker.subscriber_count

        chunks, remaining = asyncio.run(scenario())
        assert chunks[0] == f"retry: {rfp_events.RETRY_MS}\n\n"
        assert chunks[1] == ": connected\n\n"
        assert chunks[2] == ": ping\n\n"
        assert _parse(chunks[3])["event"] == "rfp_ticket_created"
        assert remaining == 0


class TestReplay:
    """Recuperación con Last-Event-ID (replay corto) o ``rfp_resync``."""

    @staticmethod
    def _missed(broker: TicketEventBroker, last_event_id: str | None) -> list[dict]:
        async def scenario():
            _, missed = broker.subscribe(last_event_id)
            return missed

        return [_parse(f) for f in asyncio.run(scenario())]

    def test_replays_only_frames_after_last_event_id(self):
        broker = TicketEventBroker()
        broker.publish_ticket(_ticket("rfp_a"), True)
        broker.publish_ticket(_ticket("rfp_a", "analisis_completo"), False)
        broker.publish_ticket(_ticket("rfp_b"), True)

        missed = self._missed(broker, f"{broker.epoch}-1")
        assert [(f["id"], f["event"], f["data"]["ticket_id"]) for f in missed] == [
            (f"{broker.epoch}-2", "rfp_ticket_updated", "rfp_a"),
            (f"{broker.epoch}-3", "rfp_ticket_created", "rfp_b"),
        ]
        assert self._missed(broker, f"{broker.epoch}-3") == []

    @pytest.mark.parametrize("last_event_id", ["otroproceso-2", "basura", "{epoch}-99"])
    def test_unknown_id_requests_resync(self, last_event_id):
        broker = TicketEventBroker()
        broker.publish_ticket(_ticket(), True)
        broker.publish_ticket(_ticket(status="descartado"), False)

        missed = self._missed(broker, last_event_id.format(epoch=broker.epoch))
        assert [f["event"] for f in missed] == ["rfp_resync"]

    def test_id_out_of_the_buffer_requests_resync(self):
        broker = TicketEventBroker(replay_size=2)
        for status in ("analizando", "analisis_completo", "generando_borrador", "en_evaluación"):
            broker.publish_ticket(_ticket(status=status), False)

        assert [f["event"] for f in self._missed(broker, f"{broker.epoch}-1")] == ["rfp_resync"]
        assert [f["data"]["status"] for f in self._missed(broker, f"{broker.epoch}-2")] == [
            "generando_borrador", "en_evaluación",
        ]

    def test_frame_is_replayed_or_queued_never_both(self):
        async def scenario():
            broker = TicketEventBroker()
            broker.publish_ticket(_ticket("rfp_a"), True)
            queue, missed = broker.subscribe(f"{broker.epoch}-0")
            broker.publish_ticket(_ticket("rfp_b"), True)
            await asyncio.sleep(0)
            return missed + [queue.get_nowait() for _ in range(queue.qsize())]

        ids = [_parse(f)["data"]["ticket_id"] for f in asyncio.run(scenario())]
        assert ids == ["rfp_a", "rfp_b"]


# ===================================================================
# HTTP real: GET /rfp/events
# ===================================================================


def test_events_endpoint_requires_the_backoffice_jwt(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    monkeypatch.setenv("JWT_SECRET_KEY", "sse-secret")
    monkeypatch.setenv("RFP_RECOVERY_INTERVAL_SECONDS", "0")
    with TestClient(app) as client:
        assert client.get("/rfp/events").status_code == 401
        bad = client.get("/rfp/events", headers={"Authorization": "Bearer no-es-un-jwt"})
        assert bad.status_code == 401


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _sse_frames(lines: Iterator[str]) -> Iterator[dict]:
    """Agrupa las líneas del wire en frames (``{"comment": ...}`` para keep-alive)."""
    frame: dict = {}
    for line in lines:
        if line == "":
            if frame:
                yield frame
            frame = {}
        elif line.startswith(":"):
            frame["comment"] = line[1:].strip()
        else:
            key, _, value = line.partition(":")
            frame[key] = value.removeprefix(" ")
    if frame:
        yield frame


def _ticket_frames(lines: Iterator[str]) -> Iterator[dict]:
    """Solo los frames con evento (``data`` ya decodificado)."""
    for frame in _sse_frames(lines):
        if "event" in frame:
            frame["data"] = json.loads(frame["data"])
            yield frame


@pytest.fixture
def live_api(tmp_path, monkeypatch):
    """API real en uvicorn con JWT real (usuario TinyDB + token HS256)."""
    from datetime import datetime, timedelta, timezone

    import jwt
    import uvicorn
    from sqlmodel import create_engine
    from tinydb import TinyDB

    import auth
    import data.pipelines.rfp_intake.db as db_mod
    import data.pipelines.rfp_intake.pipeline as pipeline_mod
    from main import app

    engine = create_engine(
        f"sqlite:///{tmp_path / 'rfp_intake.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    ).execution_options(schema_translate_map={"rfp_intake": None})
    db_mod.create_tables(engine)
    db_mod.set_engine(engine)
    monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path / "raw")
    monkeypatch.setenv("RFP_RECOVERY_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RFP_GENERATOR", "template")
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
    monkeypatch.setattr(rfp_events, "HEARTBEAT_SECONDS", 0.3)

    users_db = tmp_path / "users.json"
    with TinyDB(users_db) as tiny:
        tiny.table("users").insert(
            {"id": "u-sales", "email": "ventas@trackflow.example", "role": "admin", "is_active": True}
        )
    monkeypatch.setattr(auth, "USER_DB_PATH", users_db)
    monkeypatch.setenv("JWT_SECRET_KEY", "sse-secret")
    token = jwt.encode(
        {"sub": "u-sales", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "sse-secret",
        algorithm=auth.JWT_ALGORITHM,
    )

    server = uvicorn.Server(
        # Sin timeout, un stream SSE abierto bloquearía el apagado (Python 3.12).
        uvicorn.Config(
            app, host="127.0.0.1", port=_free_port(), log_level="warning",
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn no arrancó"

    yield {"url": f"http://127.0.0.1:{server.config.port}", "token": token}

    server.should_exit = True
    thread.join(timeout=5)
    db_mod.set_engine(None)


@pytest.fixture
def api_client(live_api):
    import httpx

    with httpx.Client(
        base_url=live_api["url"],
        headers={"Authorization": f"Bearer {live_api['token']}"},
        timeout=30,
    ) as client:
        yield client


def _upload(client) -> str:
    files = {"file": ("trackflow-request-1.pdf", SAMPLE_PDF.read_bytes(), "application/pdf")}
    response = client.post("/rfp/upload", files=files)
    assert response.status_code == 202, response.text
    return response.json()["ticket_id"]


def _wait_status(client, ticket_id: str, status: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get(f"/rfp/tickets/{ticket_id}").json()["ticket"]["status"] == status:
            return
        time.sleep(0.1)
    pytest.fail(f"{ticket_id} no llegó a {status}")


needs_sample = pytest.mark.skipif(
    not SAMPLE_PDF.exists(), reason="Ejecuta primero scripts/generate_rfp_samples.py"
)


def test_stream_without_token_gets_no_events(live_api):
    import httpx

    with httpx.Client(base_url=live_api["url"], timeout=5) as client:
        with client.stream("GET", "/rfp/events") as stream:
            assert stream.status_code == 401
            assert not stream.headers["content-type"].startswith("text/event-stream")


@needs_sample
def test_upload_is_pushed_as_named_sse_events(api_client):
    with api_client.stream("GET", "/rfp/events") as stream:
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-cache"
        lines = stream.iter_lines()
        frames = _sse_frames(lines)
        assert next(frames) == {"retry": str(rfp_events.RETRY_MS)}
        assert next(frames) == {"comment": "connected"}  # keep-alive inicial

        ticket_id = _upload(api_client)
        events = []
        done = False
        for frame in _sse_frames(lines):
            if frame.get("comment") == "ping":
                # Keep-alive sin eventos: tras el estado final, el stream sigue abierto.
                if done:
                    break
                continue
            assert "event" in frame, frame  # nunca un "message" genérico
            frame["data"] = json.loads(frame["data"])
            events.append(frame)
            done = frame["data"]["status"] == FINAL_STATUS

    created, *updates = events
    assert created["event"] == "rfp_ticket_created"
    assert created["data"]["ticket_id"] == ticket_id
    assert created["data"]["status"] == "analizando"
    assert TICKET_FIELDS | RFP_METADATA_FIELDS <= set(created["data"])
    assert created["id"].startswith(f"{rfp_events.ticket_events.epoch}-")  # id para Last-Event-ID

    assert {u["event"] for u in updates} == {"rfp_ticket_updated"}
    assert all(u["data"]["ticket_id"] == ticket_id for u in updates)
    statuses = [u["data"]["status"] for u in updates]
    assert "analisis_completo" in statuses and "generando_borrador" in statuses
    assert statuses[-1] == FINAL_STATUS
    # La fila de la tabla se pinta con el payload: metadatos ya extraídos.
    assert updates[-1]["data"]["client_name"] == "ModaViva Fashion S.L."
    assert updates[-1]["data"]["currency"] == "EUR"


@needs_sample
def test_reconnect_with_last_event_id_recovers_missed_tickets_once(api_client):
    # 1) Conectado: llega el alta del primer ticket y el cliente "se cae".
    with api_client.stream("GET", "/rfp/events") as stream:
        first_id = _upload(api_client)
        created = next(_ticket_frames(stream.iter_lines()))
    assert created["event"] == "rfp_ticket_created"
    assert created["data"]["ticket_id"] == first_id
    last_event_id = created["id"]

    # 2) Desconectado: llega otro ticket y ambos avanzan de estado.
    second_id = _upload(api_client)
    _wait_status(api_client, first_id, FINAL_STATUS)
    _wait_status(api_client, second_id, FINAL_STATUS)

    # 3) Reconexión con Last-Event-ID: replay de lo perdido hasta el keep-alive.
    replayed = []
    with api_client.stream("GET", "/rfp/events", headers={"Last-Event-ID": last_event_id}) as stream:
        for frame in _sse_frames(stream.iter_lines()):
            if frame.get("comment") == "ping":
                break
            if "event" in frame:
                frame["data"] = json.loads(frame["data"])
                replayed.append(frame)

    ids = [f["id"] for f in replayed]
    assert last_event_id not in ids and len(ids) == len(set(ids))
    created_frames = [f for f in replayed if f["event"] == "rfp_ticket_created"]
    assert [f["data"]["ticket_id"] for f in created_frames] == [second_id]  # una sola vez
    last_status = {f["data"]["ticket_id"]: f["data"]["status"] for f in replayed}
    assert last_status == {first_id: FINAL_STATUS, second_id: FINAL_STATUS}


def test_reconnect_after_server_restart_gets_explicit_resync(api_client):
    headers = {"Last-Event-ID": "procesoanterior-42"}
    with api_client.stream("GET", "/rfp/events", headers=headers) as stream:
        frame = next(_ticket_frames(stream.iter_lines()))
    assert frame["event"] == "rfp_resync"
    assert frame["data"] == {"reason": "replay_unavailable"}

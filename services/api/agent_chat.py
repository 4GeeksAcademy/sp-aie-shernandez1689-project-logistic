"""Chat en tiempo real con el agente de soporte ``first_line_cx`` (WebSocket).

Extiende ``POST /agent/query`` (petición/respuesta) con ``WS /agent/ws``: el
mismo grafo LangGraph (services/rag_agent/graph.py), pero la respuesta llega
token a token, el cliente puede interrumpirla y el operador puede pausarla,
reanudarla o cancelarla (company-brifing2.md §2).

Entidades (company-brifing2.md / company-brifing.md §2-§3)
---------------------------------------------------------
- ``agent_id`` = ``first_line_cx``; cada conversación es un flujo
  ``chat_session`` identificado por ``flow_id`` (``chat_xxxxxxxx``). El
  ``flow_id`` es a la vez el id de sesión y el ``thread_id`` de LangGraph.
- Mensajes: ``{message_id, role: user|assistant, content, status, created_at,
  reply_to}`` con ``status`` ``complete | streaming | interrupted | failed``.

Streaming del grafo: ``graph.stream(..., stream_mode=["custom", "updates"])``.

- ``custom``: el LLM se llama con httpx (data/pipelines/rag.py), no con un
  chat model de LangChain, así que el modo ``messages`` no vería tokens; los
  nodos ``generate``/``casual`` los emiten con ``get_stream_writer()``.
- ``updates``: un chunk por nodo terminado → ``generation_step`` y la
  respuesta final autoritativa (ya pasó ``validate_output``).

Productor / consumidor (pub/sub)
--------------------------------
El turno del agente (productor) corre en un hilo — el checkpointer es
síncrono — y entrega cada chunk al event loop con ``call_soon_threadsafe``.
Allí ``ChatService`` actualiza el historial de la sesión y publica el evento
en el tópico ``flow_id`` de ``ChatEventBus``. Cada WebSocket (consumidor) es
una suscripción a ese tópico: la generación no depende de que haya un socket
abierto. Si el cliente se cae, el turno sigue y al reconectar recibe
``session_restored`` (historial + generación en curso) y después los tokens
nuevos. Snapshot y suscripción ocurren en el mismo paso del loop, así que no
se pierde ni se duplica ningún token. Bus e historial viven en el proceso
(un worker); para varios workers el tópico iría a Redis pub/sub o
``LISTEN/NOTIFY``.

Protocolo (``{"command"|"event": ..., "data": {...}}``)
-------------------------------------------------------
Conexión: ``WS /agent/ws?token=<JWT>&flow_id=<chat_...>`` (la sesión se crea
antes con ``POST /agent/sessions``). Sin JWT válido, sin sesión o con una
sesión ajena el socket se cierra antes de cualquier evento de chat
(``CLOSE_*``). Excepción: un rol de control (``CONTROL_ROLES``) puede
conectarse a una sesión ajena como observador (supervisor): recibe el mismo
tópico, puede usar pause/resume/cancel, pero no ``send_message``/``interrupt``.
Ningún suscriptor dispara llamadas al agente: solo los comandos las producen,
una vez por turno, sea cual sea el número de sockets.

Cliente → servidor
- ``send_message`` ``{text}`` — si hay una respuesta en curso la interrumpe
  (``reason: new_input``) y arranca un turno nuevo con ese texto.
- ``interrupt`` ``{}`` — detiene la respuesta en curso sin texto nuevo.
- ``pause`` / ``resume`` / ``cancel`` ``{agent_id, flow_id}`` — control de
  operador (ingeniería u operaciones); ``flow_id`` puede ser de otra sesión.

Servidor → cliente (todos llevan ``agent_id`` y ``flow_id``)
- ``session_restored`` ``{flow_type, created_at, messages, generation}``
- ``message_created`` ``{message}`` — mensaje del usuario aceptado.
- ``generation_started`` ``{message_id, reply_to}``
- ``generation_step`` ``{message_id, node}``
- ``token_chunk`` ``{message_id, token}``
- ``generation_completed`` ``{message_id, content}``
- ``generation_interrupted`` ``{message_id, content, reason}`` — ``content``
  es el parcial ya mostrado; ``reason``: ``new_input | user_interrupt |
  cancelled``. Después no llega ningún ``token_chunk`` de ese ``message_id``.
- ``generation_failed`` ``{message_id, message}``
- ``control_applied`` ``{action, actor_id, timestamp}`` (briefing §4)
- ``chat_session_closed`` ``{reason}``
- ``chat_error`` ``{code, message}`` (solo a la conexión que envió el comando)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auth import CurrentUser
from data.pipelines.rag import StreamAborted
from services.rag_agent import tracing

logger = logging.getLogger(__name__)

AGENT_ID = "first_line_cx"
FLOW_TYPE = "chat_session"

COMMAND_SEND_MESSAGE = "send_message"
COMMAND_INTERRUPT = "interrupt"
ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_CANCEL = "cancel"

EVENT_SESSION_RESTORED = "session_restored"
EVENT_MESSAGE_CREATED = "message_created"
EVENT_GENERATION_STARTED = "generation_started"
EVENT_GENERATION_STEP = "generation_step"
EVENT_TOKEN_CHUNK = "token_chunk"
EVENT_GENERATION_COMPLETED = "generation_completed"
EVENT_GENERATION_INTERRUPTED = "generation_interrupted"
EVENT_GENERATION_FAILED = "generation_failed"
EVENT_CONTROL_APPLIED = "control_applied"
EVENT_SESSION_CLOSED = "chat_session_closed"
EVENT_ERROR = "chat_error"

STATUS_COMPLETE = "complete"
STATUS_STREAMING = "streaming"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"

REASON_NEW_INPUT = "new_input"
REASON_USER_INTERRUPT = "user_interrupt"
REASON_CANCELLED = "cancelled"

# Cierres antes de cualquier evento de chat (rango 4000-4999 de aplicación).
CLOSE_BAD_REQUEST = 4400  # falta flow_id
CLOSE_UNAUTHORIZED = 4401  # sin JWT o JWT inválido
CLOSE_FORBIDDEN = 4403  # la sesión es de otro usuario
CLOSE_NOT_FOUND = 4404  # flow_id desconocido (p. ej. reinicio del proceso)
CLOSE_LAGGED = 4408  # el consumidor no siguió el ritmo: reconectar y rehidratar
CLOSE_SESSION_CLOSED = 4410  # sesión cancelada por un operador

# company-brifing2.md §3: ingeniería u operaciones, desde cualquier país.
CONTROL_ROLES = frozenset(
    role.strip()
    for role in os.getenv("AGENT_CONTROL_ROLES", "engineering,operations").split(",")
    if role.strip()
)
STREAM_MODES = ["custom", "updates"]
SUBSCRIBER_QUEUE_SIZE = 1000
GENERATION_FAILED_MESSAGE = (
    "No se pudo procesar la pregunta con el agente. Inténtalo de nuevo más tarde."
)


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal["send_message", "interrupt", "pause", "resume", "cancel"]
    data: dict[str, Any] = Field(default_factory=dict)


class SendMessageData(BaseModel):
    # Mismos límites que ``AgentQueryRequest`` de POST /agent/query.
    text: str = Field(..., min_length=1, max_length=1000)


class ControlData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    flow_id: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ── Productor: turno del grafo en un hilo ─────────────────────────────────


class ChatControl:
    """Compuerta hilo-segura entre el hilo del grafo y los comandos."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._paused = False
        self._cancelled = False

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> bool:
        with self._cond:
            if self._paused or self._cancelled:
                return False
            self._paused = True
            return True

    def resume(self) -> bool:
        with self._cond:
            if not self._paused or self._cancelled:
                return False
            self._paused = False
            self._cond.notify_all()
            return True

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def gate(self) -> None:
        """Lo llama el hilo del grafo: espera si hay pausa, aborta si hay cancel."""
        with self._cond:
            while self._paused and not self._cancelled:
                self._cond.wait()
            if self._cancelled:
                raise StreamAborted("generation aborted")


def run_graph_turn(
    graph: Any,
    question: str,
    flow_id: str,
    control: ChatControl,
    post: Any,
) -> None:
    """Hilo del turno: ejecuta el grafo en streaming y publica cada chunk.

    ``post(kind, payload)`` con ``kind`` ``custom | updates`` (chunks) y
    exactamente un cierre: ``done | aborted | error``. La compuerta corta
    también el stream HTTP del LLM: ``StreamAborted`` sale de ``on_token`` y
    cierra la respuesta en streaming.
    """
    config = {
        "configurable": {
            "thread_id": flow_id,
            "stream_tokens": True,
            "token_gate": control.gate,
        }
    }
    try:
        stream = graph.stream({"question": question}, config, stream_mode=STREAM_MODES)
        try:
            for mode, chunk in stream:
                # Pausa/abort también entre nodos (respuestas de tool sin tokens).
                control.gate()
                post(mode, chunk)
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
    except StreamAborted:
        tracing.abort_run(flow_id, "interrupted")
        post("aborted", None)
    except Exception as exc:  # fallo en cualquier nodo del grafo
        logger.exception("Fallo del grafo en flow_id=%s: %s", flow_id, exc)
        tracing.abort_run(flow_id, "error")
        post("error", None)
    else:
        post("done", None)


# ── Historial de la sesión ────────────────────────────────────────────────


@dataclass
class ChatMessage:
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    status: str
    created_at: str = field(default_factory=_utc_timestamp)
    reply_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Generation:
    """Un turno del asistente: su mensaje, su compuerta y su ciclo de vida."""

    def __init__(self, message: ChatMessage, question: str) -> None:
        self.message = message
        self.question = question
        self.control = ChatControl()
        # Solo mientras ``active`` sus chunks llegan al historial y al bus.
        self.active = True
        self.answer = ""
        # El hilo del grafo terminó (el siguiente turno espera a esto para no
        # ejecutar dos corridas a la vez sobre el mismo thread_id).
        self.finished = asyncio.Event()


@dataclass
class ChatSession:
    flow_id: str
    owner_id: str
    created_at: str = field(default_factory=_utc_timestamp)
    messages: list[ChatMessage] = field(default_factory=list)
    generation: Generation | None = None
    closed: bool = False

    def ids(self, **extra: Any) -> dict[str, Any]:
        return {"agent_id": AGENT_ID, "flow_id": self.flow_id, **extra}

    @property
    def active_generation(self) -> Generation | None:
        gen = self.generation
        return gen if gen is not None and gen.active else None

    def snapshot(self) -> dict[str, Any]:
        gen = self.active_generation
        return self.ids(
            flow_type=FLOW_TYPE,
            created_at=self.created_at,
            messages=[m.to_dict() for m in self.messages],
            generation=None
            if gen is None
            else {"message_id": gen.message.message_id, "paused": gen.control.paused},
        )


# ── Bus pub/sub ───────────────────────────────────────────────────────────


class Subscription:
    def __init__(self, flow_id: str, queue_size: int) -> None:
        self.flow_id = flow_id
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=queue_size)
        self.lagged = False

    def offer(self, frame: dict[str, Any]) -> None:
        if self.lagged:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Un token perdido corrompería el mensaje: en vez de descartar,
            # se corta la suscripción y el cliente reconecta y rehidrata.
            self.lagged = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)


class ChatEventBus:
    """Tópicos por ``flow_id``. Solo se usa desde el event loop."""

    def __init__(self, queue_size: int = SUBSCRIBER_QUEUE_SIZE) -> None:
        self._topics: dict[str, set[Subscription]] = {}
        self._queue_size = queue_size

    def subscriber_count(self, flow_id: str) -> int:
        return len(self._topics.get(flow_id, ()))

    def subscribe(self, flow_id: str) -> Subscription:
        sub = Subscription(flow_id, self._queue_size)
        self._topics.setdefault(flow_id, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._topics.get(sub.flow_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._topics[sub.flow_id]

    def publish(self, flow_id: str, event: str, data: dict[str, Any]) -> None:
        frame = {"event": event, "data": data}
        for sub in list(self._topics.get(flow_id, ())):
            sub.offer(frame)


# ── Consumidor: una conexión WebSocket ────────────────────────────────────


class Connection:
    def __init__(
        self,
        websocket: WebSocket,
        user: CurrentUser,
        session: ChatSession,
        observer: bool = False,
    ) -> None:
        self.websocket = websocket
        self.user = user
        self.session = session
        # Supervisor suscrito a una sesión ajena: solo lectura + control.
        self.observer = observer
        self._send_lock = asyncio.Lock()

    async def send_frame(self, frame: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(frame)

    async def send(self, event: str, data: dict[str, Any]) -> None:
        await self.send_frame({"event": event, "data": data})

    async def send_error(self, code: str, message: str) -> None:
        await self.send(EVENT_ERROR, {"code": code, "message": message})


class ChatService:
    """Sesiones + bus: produce eventos del agente y sirve conexiones."""

    def __init__(self) -> None:
        self.sessions: dict[str, ChatSession] = {}
        self.bus = ChatEventBus()

    def create_session(self, owner_id: str) -> ChatSession:
        session = ChatSession(flow_id=_new_id("chat"), owner_id=owner_id)
        self.sessions[session.flow_id] = session
        return session

    def get(self, flow_id: str) -> ChatSession | None:
        return self.sessions.get(flow_id)

    def _publish(self, session: ChatSession, event: str, **data: Any) -> None:
        self.bus.publish(session.flow_id, event, session.ids(**data))

    # Productor ------------------------------------------------------------

    def submit_message(self, session: ChatSession, text: str, graph: Any) -> None:
        """Mensaje del usuario: interrumpe lo que esté en curso y abre un turno."""
        previous = session.generation
        self.interrupt(session, REASON_NEW_INPUT)

        user_message = ChatMessage(_new_id("msg"), "user", text, STATUS_COMPLETE)
        session.messages.append(user_message)
        self._publish(session, EVENT_MESSAGE_CREATED, message=user_message.to_dict())

        reply = ChatMessage(
            _new_id("msg"), "assistant", "", STATUS_STREAMING, reply_to=user_message.message_id
        )
        session.messages.append(reply)
        gen = Generation(reply, text)
        session.generation = gen
        self._publish(
            session,
            EVENT_GENERATION_STARTED,
            message_id=reply.message_id,
            reply_to=user_message.message_id,
        )
        asyncio.create_task(self._run(session, gen, previous, graph))

    def interrupt(self, session: ChatSession, reason: str) -> bool:
        """Aborta la generación en curso conservando el parcial ya emitido."""
        gen = session.active_generation
        if gen is None:
            return False
        gen.active = False  # desde aquí sus chunks pendientes se descartan
        gen.control.cancel()  # el hilo sale en el siguiente token o nodo
        gen.message.status = STATUS_INTERRUPTED
        self._publish(
            session,
            EVENT_GENERATION_INTERRUPTED,
            message_id=gen.message.message_id,
            content=gen.message.content,
            reason=reason,
        )
        return True

    async def _run(
        self, session: ChatSession, gen: Generation, previous: Generation | None, graph: Any
    ) -> None:
        if previous is not None:
            await previous.finished.wait()
        if not gen.active:  # interrumpida antes de empezar
            gen.finished.set()
            return
        loop = asyncio.get_running_loop()

        def post(kind: str, payload: Any) -> None:
            loop.call_soon_threadsafe(self._on_chunk, session, gen, kind, payload)

        threading.Thread(
            target=run_graph_turn,
            args=(graph, gen.question, session.flow_id, gen.control, post),
            name=f"agent-chat-{session.flow_id}",
            daemon=True,
        ).start()

    def _on_chunk(self, session: ChatSession, gen: Generation, kind: str, payload: Any) -> None:
        """En el event loop: actualiza el historial y publica en el tópico."""
        message = gen.message
        if kind in ("done", "aborted", "error"):
            gen.finished.set()
            if not gen.active:
                return
            gen.active = False
            if kind == "done":
                message.content = gen.answer or message.content
                message.status = STATUS_COMPLETE
                self._publish(
                    session,
                    EVENT_GENERATION_COMPLETED,
                    message_id=message.message_id,
                    content=message.content,
                )
            else:
                message.status = STATUS_FAILED
                self._publish(
                    session,
                    EVENT_GENERATION_FAILED,
                    message_id=message.message_id,
                    message=GENERATION_FAILED_MESSAGE,
                )
            return
        if not gen.active:
            return  # chunk ya en vuelo cuando llegó el abort
        if kind == "custom":
            token = payload.get("token") if isinstance(payload, dict) else None
            if token:
                message.content += token
                self._publish(
                    session, EVENT_TOKEN_CHUNK, message_id=message.message_id, token=token
                )
        elif kind == "updates":
            for node, output in (payload or {}).items():
                self._publish(
                    session, EVENT_GENERATION_STEP, message_id=message.message_id, node=node
                )
                if isinstance(output, dict) and output.get("answer"):
                    gen.answer = output["answer"]

    # Consumidor -------------------------------------------------------------

    async def serve(
        self,
        websocket: WebSocket,
        session: ChatSession,
        user: CurrentUser,
        graph: Any,
        observer: bool = False,
    ) -> None:
        """Rehidrata la sesión y reenvía su tópico hasta la desconexión."""
        conn = Connection(websocket, user, session, observer)
        # Snapshot + suscripción sin ``await`` entre medias: todo evento
        # posterior al snapshot llega por la cola, ninguno dos veces.
        snapshot = session.snapshot()
        sub = self.bus.subscribe(session.flow_id)
        try:
            await conn.send(EVENT_SESSION_RESTORED, snapshot)
            reader = asyncio.create_task(self._read(conn, graph))
            writer = asyncio.create_task(self._write(conn, sub))
            done, pending = await asyncio.wait(
                {reader, writer}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending | done:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        except Exception:  # cliente desconectado durante el snapshot
            pass
        finally:
            self.bus.unsubscribe(sub)

    async def _read(self, conn: Connection, graph: Any) -> None:
        while True:
            text = await conn.websocket.receive_text()
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                await conn.send_error("invalid_command", "El mensaje no es JSON válido.")
                continue
            await self.handle(conn, raw, graph)

    async def _write(self, conn: Connection, sub: Subscription) -> None:
        while True:
            frame = await sub.queue.get()
            if frame is None:
                await conn.websocket.close(code=CLOSE_LAGGED)
                return
            await conn.send_frame(frame)
            if frame["event"] == EVENT_SESSION_CLOSED:
                await conn.websocket.close(code=1000)
                return

    # Comandos ---------------------------------------------------------------

    async def handle(self, conn: Connection, raw: Any, graph: Any) -> None:
        try:
            command = ChatCommand.model_validate(raw)
        except ValidationError:
            await conn.send_error(
                "invalid_command",
                "Comando no válido: usa send_message, interrupt, pause, resume o cancel.",
            )
            return
        session = conn.session
        if conn.observer and command.command in (COMMAND_SEND_MESSAGE, COMMAND_INTERRUPT):
            await conn.send_error(
                "read_only",
                "Observas una sesión ajena: solo puedes usar pause, resume o cancel.",
            )
        elif command.command == COMMAND_SEND_MESSAGE:
            try:
                text = SendMessageData.model_validate(command.data).text.strip()
            except ValidationError:
                text = ""
            if not text:
                await conn.send_error(
                    "invalid_message", "El mensaje debe tener entre 1 y 1000 caracteres."
                )
            elif session.closed:
                await conn.send_error("session_closed", "La sesión de chat está cerrada.")
            else:
                self.submit_message(session, text, graph)
        elif command.command == COMMAND_INTERRUPT:
            if not self.interrupt(session, REASON_USER_INTERRUPT):
                await conn.send_error("invalid_state", "No hay una respuesta en curso.")
        else:
            await self._apply_control(conn, command.command, command.data)

    async def _apply_control(self, conn: Connection, action: str, data: dict[str, Any]) -> None:
        try:
            target_ids = ControlData.model_validate(data)
        except ValidationError:
            await conn.send_error(
                "invalid_command", "pause/resume/cancel requieren agent_id y flow_id."
            )
            return
        if target_ids.agent_id != AGENT_ID:
            await conn.send_error(
                "unsupported_agent", f"Este canal solo controla el agente {AGENT_ID}."
            )
            return
        if conn.user.role not in CONTROL_ROLES:
            await conn.send_error(
                "forbidden",
                "Se requiere un usuario del backoffice con rol de ingeniería u operaciones.",
            )
            return
        target = self.get(target_ids.flow_id)
        if target is None or target.closed:
            await conn.send_error(
                "flow_not_found", f"No hay sesión de chat abierta {target_ids.flow_id}."
            )
            return

        gen = target.active_generation
        if action == ACTION_PAUSE:
            applied = gen is not None and gen.control.pause()
        elif action == ACTION_RESUME:
            applied = gen is not None and gen.control.resume()
        else:
            applied = True
        if not applied:
            await conn.send_error(
                "invalid_state",
                "pause requiere una respuesta en curso; resume, una respuesta pausada.",
            )
            return

        if action == ACTION_CANCEL:
            self.interrupt(target, REASON_CANCELLED)
            target.closed = True
        event = target.ids(action=action, actor_id=conn.user.id, timestamp=_utc_timestamp())
        self.bus.publish(target.flow_id, EVENT_CONTROL_APPLIED, event)
        if target is not conn.session:
            await conn.send(EVENT_CONTROL_APPLIED, event)
        if action == ACTION_CANCEL:
            self._publish(target, EVENT_SESSION_CLOSED, reason=REASON_CANCELLED)


chat_service = ChatService()

__all__ = [
    "AGENT_ID",
    "CLOSE_BAD_REQUEST",
    "CLOSE_FORBIDDEN",
    "CLOSE_LAGGED",
    "CLOSE_NOT_FOUND",
    "CLOSE_SESSION_CLOSED",
    "CLOSE_UNAUTHORIZED",
    "CONTROL_ROLES",
    "ChatControl",
    "ChatEventBus",
    "ChatService",
    "ChatSession",
    "chat_service",
    "run_graph_turn",
]

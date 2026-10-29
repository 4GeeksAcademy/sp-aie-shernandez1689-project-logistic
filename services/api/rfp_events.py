"""Notificaciones en tiempo real de tickets RFP (Server-Sent Events).

Sustituye el polling de ``uis/backoffice/rfp``: el pipeline avisa de cada alta
o cambio del ticket (``db.add_ticket_listener``) y este broker lo reparte a
los clientes conectados a ``GET /rfp/events``.

Eventos (``event:`` del frame SSE, nunca el genérico ``message``):

- ``rfp_ticket_created`` — ticket nuevo; llega en su estado inicial ``analizando``.
- ``rfp_ticket_updated`` — cambio de estado/motivo/metadatos (Partes 1–3).
- ``rfp_resync`` — el servidor no puede reproducir lo perdido; el cliente
  debe volver a pedir ``GET /rfp/tickets``.

``data:`` es JSON con el Ticket del BRIEFING §2.3 (``ticket_id``, ``rfp_id``,
``status``, ``raw_pdf_path``, ``created_at``, ``updated_at``) + ``reason`` y,
cuando ya existen, los metadatos de la RFP que muestra la tabla
(``client_name``, ``client_country``, ``services_requested``,
``monthly_volume``, ``deadline``, ``budget_range``, ``departments_needed``,
``currency``): la UI pinta la fila sin volver a pedir datos.

Recuperación tras una caída (Last-Event-ID + replay corto): cada frame lleva
``id: <epoch>-<seq>``. Al reconectar, el cliente manda ``Last-Event-ID`` y el
servidor reenvía los frames posteriores que sigan en su buffer
(``REPLAY_BUFFER_SIZE``). Si el id es de otro proceso (reinicio: otro
``epoch``) o ya salió del buffer, envía ``rfp_resync`` en su lugar.

El pipeline escribe desde el threadpool (BackgroundTasks) y los clientes
viven en el event loop, así que la entrega cruza hilos con
``loop.call_soon_threadsafe``. Suscripción, replay y publicación comparten un
lock: cada frame llega una vez, por replay o por la cola, nunca por las dos.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TICKET_CREATED = "rfp_ticket_created"
EVENT_TICKET_UPDATED = "rfp_ticket_updated"
EVENT_RESYNC = "rfp_resync"
# Ticket del BRIEFING §2.3 + ``reason``; ``attempts`` es interno.
TICKET_EVENT_FIELDS = (
    "ticket_id",
    "rfp_id",
    "status",
    "reason",
    "raw_pdf_path",
    "created_at",
    "updated_at",
)
# Metadatos de la RFP (BRIEFING §2.3) que la tabla del backoffice muestra.
METADATA_EVENT_FIELDS = (
    "client_name",
    "client_country",
    "services_requested",
    "monthly_volume",
    "deadline",
    "budget_range",
    "departments_needed",
    "currency",
)
HEARTBEAT_SECONDS = 15.0
RETRY_MS = 3000
REPLAY_BUFFER_SIZE = 200
# Un cliente lento no frena al resto: si su cola se llena, se descarta lo más
# antiguo (al reconectar recupera con Last-Event-ID o rfp_resync).
SUBSCRIBER_QUEUE_SIZE = 100


def format_sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    """Serializa un frame SSE (``id``/``event``/``data`` + línea en blanco)."""
    lines = [] if event_id is None else [f"id: {event_id}"]
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def ticket_payload(ticket: dict[str, Any]) -> dict[str, Any]:
    """``data:`` del evento: Ticket + metadatos de la RFP si los hay."""
    payload = {key: ticket.get(key) for key in TICKET_EVENT_FIELDS}
    payload.update({key: ticket.get(key) for key in METADATA_EVENT_FIELDS})
    return payload


def _offer(queue: asyncio.Queue[str], frame: str) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(frame)


class TicketEventBroker:
    """Reparte eventos de ticket a las suscripciones SSE activas."""

    def __init__(self, replay_size: int = REPLAY_BUFFER_SIZE) -> None:
        self._subscribers: dict[asyncio.Queue[str], asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._last_seq = 0
        # Distingue ids de un proceso anterior (reinicio) → rfp_resync.
        self.epoch = uuid.uuid4().hex[:8]
        self._replay: deque[tuple[int, str]] = deque(maxlen=replay_size)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _missed_frames(self, last_event_id: str | None) -> list[str]:
        """Frames posteriores a ``last_event_id`` o un ``rfp_resync`` (con lock)."""
        if not last_event_id:
            return []
        epoch, _, raw_seq = last_event_id.strip().partition("-")
        try:
            seq = int(raw_seq)
        except ValueError:
            seq = -1
        oldest = self._replay[0][0] if self._replay else self._last_seq + 1
        if epoch != self.epoch or seq < 0 or seq > self._last_seq or seq + 1 < oldest:
            return [format_sse(EVENT_RESYNC, {"reason": "replay_unavailable"})]
        return [frame for frame_seq, frame in self._replay if frame_seq > seq]

    def subscribe(self, last_event_id: str | None = None) -> tuple[asyncio.Queue[str], list[str]]:
        """Alta de una suscripción + lo que se perdió desde ``last_event_id``."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers[queue] = asyncio.get_running_loop()
            return queue, self._missed_frames(last_event_id)

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    def publish_ticket(self, ticket: dict[str, Any], created: bool) -> None:
        """Observador del pipeline (``db.add_ticket_listener``); hilo-seguro."""
        event = EVENT_TICKET_CREATED if created else EVENT_TICKET_UPDATED
        with self._lock:
            seq = next(self._seq)
            self._last_seq = seq
            frame = format_sse(event, ticket_payload(ticket), f"{self.epoch}-{seq}")
            self._replay.append((seq, frame))
            targets = list(self._subscribers.items())
        for queue, loop in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, frame)
            except RuntimeError:  # loop cerrado: la suscripción ya no existe
                self.unsubscribe(queue)

    async def stream(
        self,
        is_disconnected: Callable[[], Awaitable[bool]],
        last_event_id: str | None = None,
        heartbeat: float | None = None,
    ) -> AsyncIterator[str]:
        """Cuerpo de la respuesta SSE hasta que el cliente se desconecta."""
        heartbeat = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
        queue, missed = self.subscribe(last_event_id)
        try:
            yield f"retry: {RETRY_MS}\n\n"
            # Keep-alive inmediato: los proxies ven bytes y el cliente sabe
            # que el stream está abierto aunque no haya eventos todavía.
            yield ": connected\n\n"
            for frame in missed:
                yield frame
            while not await is_disconnected():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            self.unsubscribe(queue)


ticket_events = TicketEventBroker()

__all__ = [
    "EVENT_RESYNC",
    "EVENT_TICKET_CREATED",
    "EVENT_TICKET_UPDATED",
    "METADATA_EVENT_FIELDS",
    "TICKET_EVENT_FIELDS",
    "TicketEventBroker",
    "format_sse",
    "ticket_events",
    "ticket_payload",
]

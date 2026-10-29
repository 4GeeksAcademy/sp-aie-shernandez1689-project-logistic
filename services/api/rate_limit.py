"""Rate limiting de las rutas que invocan un modelo (informe NIST, PR-6).

Cada usuario autenticado puede hacer como mucho ``LLM_RATE_LIMIT_PER_MINUTE``
llamadas al LLM (20 por defecto) en una ventana deslizante de 60 s. El
presupuesto es uno solo y lo comparten ``POST /agent/query``,
``POST /api/rag/query``, ``POST /knowledge/query`` y los ``send_message`` del
chat WebSocket, para que no se pueda esquivar cambiando de vía. Al superarlo:
HTTP 429 con ``Retry-After`` o ``chat_error`` ``rate_limited`` en el WS.

El estado vive en memoria del proceso (mismo supuesto de un worker que el bus
del chat y el broker SSE): con varios workers el límite efectivo se
multiplica y habría que moverlo a Redis.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Callable

from fastapi import Depends, HTTPException, status

from auth import CurrentUser, get_current_user

WINDOW_SECONDS = 60.0
DEFAULT_LIMIT_PER_MINUTE = 20


def limit_per_minute() -> int:
    """``LLM_RATE_LIMIT_PER_MINUTE``; se lee en cada llamada para poder ajustarlo."""
    try:
        return max(1, int(os.getenv("LLM_RATE_LIMIT_PER_MINUTE", DEFAULT_LIMIT_PER_MINUTE)))
    except ValueError:
        return DEFAULT_LIMIT_PER_MINUTE


class SlidingWindowLimiter:
    """Ventana deslizante por clave; seguro entre hilos (rutas sync en threadpool)."""

    def __init__(
        self,
        limit: Callable[[], int] = limit_per_minute,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Registra una llamada. ``None`` si se admite; si no, segundos hasta poder reintentar."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit():
                return self._window - (now - hits[0])
            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


llm_limiter = SlidingWindowLimiter()


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait))


def llm_rate_limit(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Dependencia: exige JWT y consume una llamada del presupuesto del usuario."""
    wait = llm_limiter.hit(current_user.id)
    if wait is not None:
        seconds = retry_after_seconds(wait)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Límite de consultas al modelo alcanzado. Reintenta en {seconds} s.",
            headers={"Retry-After": str(seconds)},
        )
    return current_user

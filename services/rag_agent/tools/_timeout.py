"""Helper compartido: timeout numérico con SIGALRM para tools del agente.

Extraído de incident_lookup para que todas las tools (tickets, inventario)
apliquen exactamente las mismas reglas de timeout/fallback:
el grafo nunca se cuelga porque la operación de lectura está acotada.


Uso::

    from services.rag_agent.tools._timeout import run_with_timeout

    try:
        value = run_with_timeout(lambda: _get_reader().read(query), timeout_s)
    except TimeoutError:
        return _fallback("no respondió a tiempo ...")

Si no se puede instalar SIGALRM (p. ej. fuera del hilo principal o en
plataformas sin la señal), se ejecuta sin timeout: las lecturas locales
(TinyDB / SQLite) no bloquean y los tests pueden forzar la ruta de
fallback pidiendo ``timeout=0``.
"""

from __future__ import annotations

import signal
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """Ejecuta ``fn`` con un timeout absoluto (SIGALRM en el hilo principal).

    Si el timeout expira se lanza TimeoutError. Los segundos deben ser
    un número positivo explícito; ``None`` o ``<= 0`` lanzan TimeoutError
    inmediatamente (el contrato exige un timeout numérico definido).
    """
    if timeout is None or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise TimeoutError("timeout no positivo")
    if hasattr(signal, "SIGALRM"):
        old_handler = signal.signal(signal.SIGALRM, _raise_timeout)
        # setitimer devuelve (delay_previo, interval_previo); restaurar ambos.
        previous = signal.setitimer(signal.ITIMER_REAL, float(timeout))
        try:
            return fn()
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous)
            signal.signal(signal.SIGALRM, old_handler)
    return fn()


def _raise_timeout(signum: int, frame: Any) -> None:  # pragma: no cover
    raise TimeoutError("timeout en tool del agente")
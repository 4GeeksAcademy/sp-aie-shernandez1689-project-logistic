"""Checkpointer persistente de los grafos LangGraph del monorepo.

Lo usan el grafo del agente (``services/rag_agent/graph.py``) y los grafos de
aprobación de RFPs (``data/pipelines/rfp_intake/approval_graph.py``). Vive en
``data/pipelines`` para que los pipelines no importen de ``services/``.

El estado de cada ``thread_id`` (p. ej. ``_memory_proposal`` pendiente de
confirmación, o una sección de RFP esperando a su responsable) debe
sobrevivir a reinicios y repartirse entre workers, así que el backend se
elige por entorno:

- ``postgres``: ``PostgresSaver`` sobre un pool de psycopg. Usa
  ``AGENT_CHECKPOINT_DB_URL`` o, en su defecto, ``DATABASE_URL`` (Supabase).
  Es el backend por defecto cuando hay URL de base de datos.
- ``sqlite``: ``SqliteSaver`` en un fichero local
  (``AGENT_CHECKPOINT_SQLITE_PATH``, por defecto
  ``data/checkpoints/agent_checkpoints.sqlite``). Por defecto sin URL.
- ``memory``: ``MemorySaver``. Solo para desarrollo local; pierde el estado
  al reiniciar el proceso y nunca se elige implícitamente.

``AGENT_CHECKPOINTER`` fuerza el backend. Las tablas se crean con
``setup()`` (idempotente) al construir el checkpointer. ``get_checkpointer()``
devuelve una instancia única por proceso (un solo pool/conexión para todos
los grafos); los ``thread_id`` de cada grafo van con prefijo propio.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_SHARED: BaseCheckpointSaver | None = None
_SHARED_LOCK = threading.Lock()
DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "checkpoints" / "agent_checkpoints.sqlite"
BACKENDS = ("postgres", "sqlite", "memory")


def _database_url() -> str | None:
    return os.getenv("AGENT_CHECKPOINT_DB_URL") or os.getenv("DATABASE_URL") or None


def resolve_backend() -> str:
    """Backend explícito de ``AGENT_CHECKPOINTER`` o el implícito según entorno."""
    backend = os.getenv("AGENT_CHECKPOINTER", "").strip().lower()
    if not backend:
        return "postgres" if _database_url() else "sqlite"
    if backend not in BACKENDS:
        raise ValueError(
            f"AGENT_CHECKPOINTER={backend!r} no válido; usa uno de {', '.join(BACKENDS)}"
        )
    return backend


def _postgres_saver() -> BaseCheckpointSaver:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    url = _database_url()
    if not url:
        raise RuntimeError(
            "AGENT_CHECKPOINTER=postgres requiere AGENT_CHECKPOINT_DB_URL o DATABASE_URL"
        )
    # Una URL SQLAlchemy (postgresql+psycopg://) no la entiende libpq.
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=int(os.getenv("AGENT_CHECKPOINT_POOL_SIZE", "5")),
        # prepare_threshold=None: sin prepared statements, compatible con el
        # pooler de Supabase (pgbouncer en modo transacción).
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        open=True,
    )
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


def _sqlite_saver() -> BaseCheckpointSaver:
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(os.getenv("AGENT_CHECKPOINT_SQLITE_PATH") or DEFAULT_SQLITE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI ejecuta las rutas síncronas en un
    # threadpool; SqliteSaver serializa el acceso con su propio lock.
    saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    saver.setup()
    return saver


def build_checkpointer() -> BaseCheckpointSaver:
    backend = resolve_backend()
    if backend == "postgres":
        saver = _postgres_saver()
    elif backend == "sqlite":
        saver = _sqlite_saver()
    else:
        from langgraph.checkpoint.memory import MemorySaver

        logger.warning(
            "Checkpointer en memoria: el estado de los threads se pierde al "
            "reiniciar. Úsalo solo en desarrollo local."
        )
        saver = MemorySaver()
    logger.info("Checkpointer de LangGraph: %s", backend)
    return saver


def get_checkpointer() -> BaseCheckpointSaver:
    """Checkpointer compartido del proceso (se construye en el primer uso)."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = build_checkpointer()
        return _SHARED


def set_checkpointer(saver: BaseCheckpointSaver | None) -> None:
    """Sustituye el checkpointer compartido (tests: simular un reinicio)."""
    global _SHARED
    with _SHARED_LOCK:
        _SHARED = saver

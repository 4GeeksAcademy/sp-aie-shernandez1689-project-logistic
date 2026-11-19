"""Selección del checkpointer del agente (data/pipelines/checkpointer.py).

- Sin AGENT_CHECKPOINTER: Postgres si hay URL de BD, SQLite si no; nunca memoria.
- SQLite persiste el estado de un thread entre instancias del saver (reinicio).
- Memoria solo si se pide explícitamente.
- Postgres contra un servidor real solo si AGENT_CHECKPOINT_TEST_PG_URL está
  definida (se omite en CI sin base de datos).
"""

from __future__ import annotations

import os
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from data.pipelines import checkpointer as ckpt

ENV_VARS = (
    "AGENT_CHECKPOINTER",
    "AGENT_CHECKPOINT_DB_URL",
    "AGENT_CHECKPOINT_SQLITE_PATH",
    "DATABASE_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _State(TypedDict):
    count: int


def _counter_graph(saver):
    builder = StateGraph(_State)
    builder.add_node("inc", lambda s: {"count": s["count"] + 1})
    builder.add_edge(START, "inc")
    builder.add_edge("inc", END)
    return builder.compile(checkpointer=saver)


def test_default_without_url_is_sqlite(clean_env):
    assert ckpt.resolve_backend() == "sqlite"


def test_default_with_database_url_is_postgres(clean_env):
    clean_env.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    assert ckpt.resolve_backend() == "postgres"


def test_explicit_backend_wins(clean_env):
    clean_env.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    clean_env.setenv("AGENT_CHECKPOINTER", " SQLite ")
    assert ckpt.resolve_backend() == "sqlite"


def test_invalid_backend_raises(clean_env):
    clean_env.setenv("AGENT_CHECKPOINTER", "redis")
    with pytest.raises(ValueError, match="AGENT_CHECKPOINTER"):
        ckpt.resolve_backend()


def test_postgres_without_url_raises(clean_env):
    clean_env.setenv("AGENT_CHECKPOINTER", "postgres")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        ckpt.build_checkpointer()


def test_memory_only_when_explicit(clean_env, caplog):
    clean_env.setenv("AGENT_CHECKPOINTER", "memory")
    assert isinstance(ckpt.build_checkpointer(), MemorySaver)
    assert "desarrollo local" in caplog.text


def test_sqlite_state_survives_restart(clean_env, tmp_path):
    path = tmp_path / "nested" / "ckpt.sqlite"
    clean_env.setenv("AGENT_CHECKPOINT_SQLITE_PATH", str(path))
    config = {"configurable": {"thread_id": "t-1"}}

    first = ckpt.build_checkpointer()
    assert isinstance(first, SqliteSaver)
    _counter_graph(first).invoke({"count": 0}, config)
    first.conn.close()

    # Nueva instancia sobre el mismo fichero = proceso reiniciado.
    second = ckpt.build_checkpointer()
    assert path.exists()
    assert _counter_graph(second).get_state(config).values == {"count": 1}
    second.conn.close()


@pytest.mark.skipif(
    not os.getenv("AGENT_CHECKPOINT_TEST_PG_URL"),
    reason="AGENT_CHECKPOINT_TEST_PG_URL no definida",
)
def test_postgres_state_survives_restart(clean_env):
    clean_env.setenv("AGENT_CHECKPOINTER", "postgres")
    clean_env.setenv("AGENT_CHECKPOINT_DB_URL", os.environ["AGENT_CHECKPOINT_TEST_PG_URL"])
    config = {"configurable": {"thread_id": f"t-{os.getpid()}"}}

    first = ckpt.build_checkpointer()
    _counter_graph(first).invoke({"count": 0}, config)
    first.conn.close()

    second = ckpt.build_checkpointer()
    assert _counter_graph(second).get_state(config).values == {"count": 1}
    second.conn.close()

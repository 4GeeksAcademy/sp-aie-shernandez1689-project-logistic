"""Tests para el servicio de consolidación de memoria.

Cobertura
---------
- MemoryConsolidationService.consolidate: rate-limit, sync cleanup,
  deduplicación, compresión de episodios, poda.
- Helpers: _normalize_text, _is_near_duplicate.
- ConsolidationReport: estructura de datos.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory.consolidation import (
    MemoryConsolidationService,
    _normalize_text,
    ConsolidationReport,
)


# ── Helper: ejecutar coros sin depender de pytest-asyncio ────────────


def _await(coro):
    """Ejecuta una coroutine de forma síncrona."""
    return asyncio.run(coro)


# ── Fixture: MockMemoryService ────────────────────────────────────────


@pytest.fixture
def mock_memory() -> MagicMock:
    """Retorna un MemoryService mockeado (sin conexiones reales)."""
    m = MagicMock()
    m._kv = MagicMock()
    m._kv.search_keys = AsyncMock(return_value=[])
    m._vec = MagicMock()
    m._vec.get_all_points.return_value = []
    m._vec.get_points_by_type.return_value = []
    m._vec.delete_points = MagicMock()
    m._vec.store = AsyncMock(return_value="new-point-id")
    return m


@pytest.fixture
def svc(mock_memory: MagicMock) -> MemoryConsolidationService:
    """Instancia MemoryConsolidationService con memoria mockeada (sin rate-limit)."""
    return MemoryConsolidationService(
        memory=mock_memory,
        rate_limit_hours=0,
    )


# ── Tests ─────────────────────────────────────────────────────────────


class TestConsolidationReport:
    """Estructura básica del reporte."""

    def test_default_values(self):
        report = ConsolidationReport()
        assert report.synced_orphans == 0
        assert report.dedup_removed == 0
        assert report.dedup_kept == 0
        assert report.episodes_compressed == 0
        assert report.low_relevance_pruned == 0
        assert report.errors == []
        assert report.duration_ms == 0.0
        assert report.timestamp == ""


class TestNormalizeText:
    """_normalize_text helper."""

    def test_basic_lowercase(self):
        assert _normalize_text("Hola Mundo") == "hola mundo"

    def test_extra_spaces(self):
        assert _normalize_text("  Hola   Mundo  ") == "hola mundo"

    def test_newlines_replaced(self):
        assert _normalize_text("Hola\nMundo\nFoo") == "hola mundo foo"

    def test_truncation_long(self):
        text = "a" * 600
        normalized = _normalize_text(text)
        assert len(normalized) <= 500

    def test_empty_string(self):
        assert _normalize_text("") == ""
        assert _normalize_text("   ") == ""


class TestConsolidationRateLimit:
    """El rate-limit impide ejecutar consolidate() dos veces seguidas."""

    def test_rate_limit_respects_min_interval(self, mock_memory):
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=1)
        report = _await(svc.consolidate(force=False))
        assert len(report.errors) == 0

        report = _await(svc.consolidate(force=False))
        assert any("Rate-limited" in e for e in report.errors)

    def test_force_bypasses_rate_limit(self, mock_memory):
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=1)
        _await(svc.consolidate(force=False))
        report = _await(svc.consolidate(force=True))
        assert len(report.errors) == 0

    def test_rate_limit_custom_hours(self, mock_memory):
        """Rate-limit 0h = sin límite."""
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        _await(svc.consolidate(force=False))
        report = _await(svc.consolidate(force=False))
        assert len(report.errors) == 0


class TestConsolidationSyncCleanup:
    """Sync cleanup: eliminar huérfanos de Qdrant sin clave KV."""

    def test_no_orphans(self, svc):
        """Sin huérfanos, no se elimina nada."""
        svc._memory._vec.get_all_points.return_value = []
        svc._memory._kv.search_keys = AsyncMock(return_value=[])
        removed = _await(svc.sync_cleanup_only())
        assert removed == 0

    def test_all_points_have_kv_keys(self, svc):
        """Puntos con clave KV existente no se eliminan."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "metadata": {"key": "person:role:ana"}, "memory_type": "fact"},
            {"id": "p2", "metadata": {"key": "person:role:juan"}, "memory_type": "fact"},
        ]
        svc._memory._kv.search_keys = AsyncMock(return_value=[
            "agent_memory:fact:person:role:ana",
            "agent_memory:fact:person:role:juan",
        ])
        removed = _await(svc.sync_cleanup_only())
        assert removed == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_orphans_removed(self, svc):
        """Puntos sin clave KV correspondiente se eliminan."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "metadata": {"key": "person:role:ana"}, "memory_type": "fact"},
            {"id": "p2", "metadata": {"key": "person:role:gone"}, "memory_type": "fact"},
        ]
        svc._memory._kv.search_keys = AsyncMock(return_value=[
            "agent_memory:fact:person:role:ana",
        ])
        removed = _await(svc.sync_cleanup_only())
        assert removed == 1
        svc._memory._vec.delete_points.assert_called_once_with(["p2"])

    def test_orphan_skipped_if_no_key_in_metadata(self, svc):
        """Puntos sin metadata.key no se consideran huérfanos."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "metadata": {}, "memory_type": "episode"},
            {"id": "p2", "metadata": None, "memory_type": "episode"},
        ]
        svc._memory._kv.search_keys = AsyncMock(return_value=[])
        removed = _await(svc.sync_cleanup_only())
        assert removed == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_empty_points_list(self, svc):
        """Lista vacía de puntos no causa error."""
        svc._memory._vec.get_all_points.return_value = []
        svc._memory._kv.search_keys = AsyncMock(return_value=[])
        removed = _await(svc.sync_cleanup_only())
        assert removed == 0


class TestConsolidationDeduplicate:
    """Deduplicación vectorial."""

    def test_no_duplicates(self, svc):
        """Puntos diferentes no se fusionan."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "memory_type": "fact", "text": "Ana es Gerente",
             "metadata": {"key": "person:role:ana"}, "created_at": "2026-10-27T10:00:00"},
            {"id": "p2", "memory_type": "fact", "text": "Juan es Líder",
             "metadata": {"key": "person:role:juan"}, "created_at": "2026-10-27T10:00:00"},
        ]
        result = _await(svc._deduplicate())
        assert result["removed"] == 0
        assert result["kept"] == 0

    def test_duplicate_by_key(self, svc):
        """Misma clave semántica → duplicado."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "memory_type": "fact", "text": "Ana es Gerente",
             "metadata": {"key": "person:role:ana"}, "created_at": "2026-10-27T12:00:00"},
            {"id": "p2", "memory_type": "fact", "text": "Ana es Gerente",
             "metadata": {"key": "person:role:ana"}, "created_at": "2026-10-27T10:00:00"},
        ]
        result = _await(svc._deduplicate())
        assert result["removed"] == 1
        assert result["kept"] == 1

    def test_no_duplicates_different_types(self, svc):
        """Tipos diferentes no se fusionan aunque tengan texto similar."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "memory_type": "fact", "text": "Ana es Gerente",
             "metadata": {"key": "person:role:ana"}, "created_at": "2026-10-27T10:00:00"},
            {"id": "p2", "memory_type": "episode", "text": "Ana es Gerente",
             "metadata": {}, "created_at": "2026-10-27T10:00:00"},
        ]
        result = _await(svc._deduplicate())
        assert result["removed"] == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_single_point_no_dedup(self, svc):
        """Un solo punto en un tipo no produce deduplicación."""
        svc._memory._vec.get_all_points.return_value = [
            {"id": "p1", "memory_type": "fact", "text": "Ana es Gerente",
             "metadata": {"key": "person:role:ana"}, "created_at": "2026-10-27T10:00:00"},
        ]
        result = _await(svc._deduplicate())
        assert result["removed"] == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_empty_points_no_dedup(self, svc):
        """Sin puntos, no hay deduplicación."""
        svc._memory._vec.get_all_points.return_value = []
        result = _await(svc._deduplicate())
        assert result["removed"] == 0
        assert result["kept"] == 0


class TestIsNearDuplicate:
    """_is_near_duplicate helper."""

    def test_same_key(self):
        a = {"memory_type": "fact", "metadata": {"key": "person:role:ana"}, "text": "X"}
        b = {"memory_type": "fact", "metadata": {"key": "person:role:ana"}, "text": "Y"}
        assert MemoryConsolidationService._is_near_duplicate(a, b)

    def test_different_types(self):
        a = {"memory_type": "fact", "metadata": {"key": "k"}, "text": "X"}
        b = {"memory_type": "episode", "metadata": {"key": "k"}, "text": "X"}
        assert not MemoryConsolidationService._is_near_duplicate(a, b)

    def test_same_normalized_text(self):
        a = {"memory_type": "fact", "metadata": {}, "text": "Ana  es   Gerente"}
        b = {"memory_type": "fact", "metadata": {}, "text": "Ana es Gerente"}
        assert MemoryConsolidationService._is_near_duplicate(a, b)

    def test_no_metadata(self):
        a = {"memory_type": "fact", "metadata": None, "text": "Hola"}
        b = {"memory_type": "fact", "metadata": None, "text": "Hola"}
        assert MemoryConsolidationService._is_near_duplicate(a, b)

    def test_empty_text_returns_false(self):
        a = {"memory_type": "fact", "metadata": {}, "text": ""}
        b = {"memory_type": "fact", "metadata": {}, "text": ""}
        assert not MemoryConsolidationService._is_near_duplicate(a, b)


class TestConsolidationCompressEpisodes:
    """Compresión de episodios."""

    def test_no_old_episodes(self, svc):
        svc._memory._vec.get_points_by_type.return_value = []
        result = _await(svc._compress_episodes())
        assert result == 0
        svc._memory._vec.store.assert_not_called()
        svc._memory._vec.delete_points.assert_not_called()

    def test_one_episode_no_compression(self, svc):
        """Un solo episodio no se comprime (no vale la pena)."""
        old_date = "2026-03-01T10:00:00+00:00"
        svc._memory._vec.get_points_by_type.return_value = [
            {"id": "ep1", "memory_type": "episode", "text": "Consulta sobre stock",
             "metadata": {"session_id": "s1"}, "created_at": old_date},
        ]
        result = _await(svc._compress_episodes())
        assert result == 0
        svc._memory._vec.store.assert_not_called()
        svc._memory._vec.delete_points.assert_not_called()

    def test_two_episodes_same_month_compressed(self, svc):
        old_date = "2026-03-05T10:00:00+00:00"
        svc._memory._vec.get_points_by_type.return_value = [
            {"id": "ep1", "memory_type": "episode", "text": "Consulta sobre stock",
             "metadata": {"session_id": "s1"}, "created_at": old_date},
            {"id": "ep2", "memory_type": "episode", "text": "Política de devoluciones",
             "metadata": {"session_id": "s2"}, "created_at": old_date},
        ]
        result = _await(svc._compress_episodes())
        assert result == 2
        svc._memory._vec.store.assert_called_once()
        svc._memory._vec.delete_points.assert_called_once_with(["ep1", "ep2"])

    def test_episodes_different_months_separate(self, svc):
        svc._memory._vec.get_points_by_type.return_value = [
            {"id": "ep1", "memory_type": "episode", "text": "Stock",
             "metadata": {"session_id": "s1"}, "created_at": "2026-03-05T10:00:00+00:00"},
            {"id": "ep2", "memory_type": "episode", "text": "Devoluciones",
             "metadata": {"session_id": "s2"}, "created_at": "2026-04-01T10:00:00+00:00"},
        ]
        result = _await(svc._compress_episodes())
        assert result == 0  # 1 per month, not worth compressing


class TestConsolidationPruneLowRelevance:
    """Poda de baja relevancia."""

    def test_no_points_to_prune(self, svc):
        svc._memory._vec.get_points_by_type.side_effect = [[], [], []]
        result = _await(svc._prune_low_relevance())
        assert result == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_recent_point_not_pruned(self, svc):
        now = datetime.now(timezone.utc)
        recent = now.isoformat()
        svc._memory._vec.get_points_by_type.side_effect = [
            [
                {"id": "p1", "memory_type": "fact",
                 "metadata": {"key": "person:role:ana", "last_recalled": recent},
                 "created_at": "2026-01-01T10:00:00"},
            ],
            [],
            [],
        ]
        result = _await(svc._prune_low_relevance())
        assert result == 0
        svc._memory._vec.delete_points.assert_not_called()

    def test_old_point_without_recalled_pruned(self, svc):
        svc._memory._vec.get_points_by_type.side_effect = [
            [
                {"id": "p1", "memory_type": "fact",
                 "metadata": {"key": "person:role:ana"},
                 "created_at": "2026-10-27T10:00:00"},
                {"id": "p2", "memory_type": "fact",
                 "metadata": {"key": "person:role:ana"},
                 "created_at": "2026-01-01T10:00:00"},
            ],
            [],
            [],
        ]
        result = _await(svc._prune_low_relevance())
        assert result == 1
        svc._memory._vec.delete_points.assert_called_once_with(["p2"])

    def test_single_point_not_pruned(self, svc):
        svc._memory._vec.get_points_by_type.side_effect = [
            [
                {"id": "p1", "memory_type": "fact",
                 "metadata": {"key": "person:role:ana"},
                 "created_at": "2026-01-01T10:00:00"},
            ],
            [],
            [],
        ]
        result = _await(svc._prune_low_relevance())
        assert result == 0
        svc._memory._vec.delete_points.assert_not_called()


class TestConsolidationIntegration:
    """Integración de consolidate() con todas las estrategias."""

    def test_full_consolidation_with_all_mocks(self, mock_memory):
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        with patch.object(svc, "_sync_cleanup", new=AsyncMock(return_value=3)), \
             patch.object(svc, "_deduplicate", new=AsyncMock(return_value={"removed": 2, "kept": 5})), \
             patch.object(svc, "_compress_episodes", new=AsyncMock(return_value=4)), \
             patch.object(svc, "_prune_low_relevance", new=AsyncMock(return_value=1)):
            report = _await(svc.consolidate(force=False))
            assert report.synced_orphans == 3
            assert report.dedup_removed == 2
            assert report.dedup_kept == 5
            assert report.episodes_compressed == 4
            assert report.low_relevance_pruned == 1
            assert len(report.errors) == 0
            assert report.duration_ms > 0
            assert report.timestamp != ""

    def test_consolidation_error_handling(self, mock_memory):
        """Si una estrategia falla, las demás continúan."""
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        with patch.object(svc, "_sync_cleanup", new=AsyncMock(side_effect=ValueError("Fallo en sync"))), \
             patch.object(svc, "_deduplicate", new=AsyncMock(return_value={"removed": 0, "kept": 0})), \
             patch.object(svc, "_compress_episodes", new=AsyncMock(return_value=0)), \
             patch.object(svc, "_prune_low_relevance", new=AsyncMock(return_value=0)):
            report = _await(svc.consolidate(force=False))
            assert report.synced_orphans == 0
            assert len(report.errors) == 1
            assert "Fallo en sync" in report.errors[0]

    def test_consolidation_timestamp(self, mock_memory):
        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        report = _await(svc.consolidate(force=False))
        assert report.timestamp >= "2026"
        assert "T" in report.timestamp


class TestAuditConsolidation:
    """El audit log registra los eventos de consolidación."""

    def test_audit_written_on_consolidate(self, mock_memory, tmp_path):
        test_path = tmp_path / "audit" / "memory_proposals.jsonl"
        test_path.parent.mkdir(parents=True, exist_ok=True)

        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        with patch("services.memory.consolidation._AUDIT_LOG_PATH", test_path):
            _await(svc.consolidate(force=False))

        assert test_path.exists()
        lines = test_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["event"] == "consolidation"
        assert entry["thread_id"] == "__system__"
        assert entry["user_id"] is None

    def test_audit_includes_duration(self, mock_memory, tmp_path):
        test_path = tmp_path / "audit" / "memory_proposals.jsonl"
        test_path.parent.mkdir(parents=True, exist_ok=True)

        svc = MemoryConsolidationService(memory=mock_memory, rate_limit_hours=0)
        with patch("services.memory.consolidation._AUDIT_LOG_PATH", test_path):
            _await(svc.consolidate(force=False))

        entry = json.loads(test_path.read_text().strip().split("\n")[-1])
        result = entry["result"]
        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], (int, float))
        assert result["duration_ms"] > 0
"""Servicio de consolidación y limpieza de memoria.

Política de consolidación
=========================

La memoria del agente tiene dos stores (KV + vectorial) que pueden
desincronizarse si no hay un mecanismo de limpieza coordinado. Este
módulo implementa cuatro estrategias complementarias:

1. **Sync cleanup (Qdrant → Redis)**
   Los puntos vectoriales cuya clave KV asociada ya no existe
   (por expiración TTL o eliminación explícita) se eliminan de
   Qdrant. Sin este paso, la memoria vectorial acumula "huérfanos"
   que contaminan las búsquedas semánticas.

2. **Deduplicación vectorial**
   Puntos en Qdrant con similitud coseno > 0.95 y texto casi
   idéntico se fusionan: se conserva el más reciente y se elimina
   el más antiguo. Esto evita que un mismo hecho memorizado varias
   veces (p.ej. "Ana es Gerente" confirmado en dos sesiones) duplique
   resultados de búsqueda.

3. **Compresión de episodios**
   Episodios con más de 15 días de antigüedad se fusionan en
   resúmenes mensuales condensados. Un episodio mensual ocupa ~1/3
   del espacio de N episodios diarios y sigue siendo útil para
   búsqueda semántica ("¿qué pasó en marzo?").

4. **Poda de baja relevancia**
   Puntos vectoriales cuyo ``last_recalled`` (última vez que
   aparecieron en resultados de búsqueda semántica) supera los
   60 días se eliminan. La premisa: si el agente no ha necesitado
   ese hecho en 2 meses, probablemente ya no es relevante.

Ejecución
---------
La consolidación NO se ejecuta en cada turno del grafo (sería muy
costosa). Se activa mediante:

- **Programación externa** (cron / scheduler) para ejecución nocturna.
- **Trigger manual** vía endpoint o tool del agente.
- **Nodo opcional en el grafo** solo si han pasado > 6h desde la
  última consolidación (rate-limited).

Auditoría
---------
Cada operación de consolidación se registra en el mismo audit log
que las propuestas de memoria (``data/audit/memory_proposals.jsonl``)
con evento ``consolidation`` y detalle de puntos afectados.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.memory import MemoryService
from services.memory.kv import PREFIX_FACT

logger = logging.getLogger(__name__)

# ── Ruta del log de auditoría (misma que memory_nodes.py) ────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_LOG_PATH = REPO_ROOT / "data" / "audit" / "memory_proposals.jsonl"


# ── Umbrales por defecto ─────────────────────────────────────────────

_DEFAULT_DEDUP_SIMILARITY = 0.95          # similitud coseno para considerar duplicado
_DEFAULT_EPISODE_AGE_DAYS = 15            # episodios más viejos que esto → comprimir
_DEFAULT_LOW_RELEVANCE_DAYS = 60          # sin recall en este tiempo → prunear
_DEFAULT_RATE_LIMIT_HOURS = 6             # mínimo entre consolidaciones
_MAX_DEDUP_TEXT_LENGTH = 500              # para comparación de texto (truncar)
_EPISODE_MEMORY_TYPES = {"episode"}       # tipos vectoriales que son episodios


# ── Resultado ─────────────────────────────────────────────────────────


@dataclass
class ConsolidationReport:
    """Reporte de una ejecución de consolidación."""

    synced_orphans: int = 0          # puntos huérfanos eliminados de Qdrant
    dedup_removed: int = 0           # duplicados fusionados
    dedup_kept: int = 0              # puntos conservados tras dedup
    episodes_compressed: int = 0     # episodios comprimidos en mensuales
    low_relevance_pruned: int = 0    # puntos de baja relevancia eliminados
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: str = ""


# ── ConsolidationService ──────────────────────────────────────────────


class MemoryConsolidationService:
    """Servicio de consolidación y limpieza de memoria.

    Opera sobre ambas capas de memoria (KV + vectorial) de forma
    coordinada. Cada estrategia es ejecutable de forma independiente
    o en conjunto via ``consolidate()``.

    Uso típico
    ----------
        consolidation = MemoryConsolidationService()
        report = await consolidation.consolidate()
        logger.info("Consolidación completada: %s", report)
    """

    def __init__(
        self,
        memory: MemoryService | None = None,
        dedup_similarity: float = _DEFAULT_DEDUP_SIMILARITY,
        episode_age_days: int = _DEFAULT_EPISODE_AGE_DAYS,
        low_relevance_days: int = _DEFAULT_LOW_RELEVANCE_DAYS,
        rate_limit_hours: int = _DEFAULT_RATE_LIMIT_HOURS,
    ) -> None:
        self._memory = memory or MemoryService()
        self._dedup_similarity = dedup_similarity
        self._episode_age_days = episode_age_days
        self._low_relevance_days = low_relevance_days
        self._rate_limit_hours = rate_limit_hours
        self._last_run: float = 0.0  # timestamp UNIX de última ejecución

    # ── Público ───────────────────────────────────────────────────────

    async def consolidate(self, force: bool = False) -> ConsolidationReport:
        """Ejecuta todas las estrategias de consolidación.

        Args:
            force: Si False, respeta el rate-limit (mínimo N horas entre
                   ejecuciones). Si True, fuerza la ejecución.

        Returns:
            ConsolidationReport con estadísticas de la operación.
        """
        if not force:
            elapsed = time.time() - self._last_run
            if elapsed < self._rate_limit_hours * 3600:
                remaining = int(self._rate_limit_hours * 3600 - elapsed)
                logger.info(
                    "Consolidación saltada: última ejecución hace %ds, "
                    " mínimo %dh entre ejecuciones",
                    int(elapsed), self._rate_limit_hours,
                )
                report = ConsolidationReport(timestamp=_now_iso())
                report.errors.append(
                    f"Rate-limited: faltan {remaining}s para la próxima ejecución"
                )
                return report

        start = time.time()
        report = ConsolidationReport(timestamp=_now_iso())

        try:
            # 1. Sync cleanup: eliminar huérfanos de Qdrant
            report.synced_orphans = await self._sync_cleanup()

            # 2. Deduplicación vectorial
            dedup_result = await self._deduplicate()
            report.dedup_removed = dedup_result["removed"]
            report.dedup_kept = dedup_result["kept"]

            # 3. Compresión de episodios
            report.episodes_compressed = await self._compress_episodes()

            # 4. Poda de baja relevancia
            report.low_relevance_pruned = await self._prune_low_relevance()

        except Exception as exc:
            logger.exception("Error durante consolidación: %s", exc)
            report.errors.append(str(exc))

        report.duration_ms = (time.time() - start) * 1000
        self._last_run = time.time()
        self._audit(report)
        return report

    async def sync_cleanup_only(self) -> int:
        """Ejecuta solo la limpieza de huérfanos.

        Útil para ejecución frecuente (cada hora) desde scheduler.
        """
        return await self._sync_cleanup()

    # ── Estrategias privadas ──────────────────────────────────────────

    async def _sync_cleanup(self) -> int:
        """Elimina de Qdrant los puntos cuya clave KV ya no existe.

        Funcionamiento:
        1. Obtiene todas las claves KV activas que tengan referencia
           en Qdrant (a través del metadata ``key``).
        2. Para cada punto Qdrant, verifica si la clave KV asociada
           sigue viva. Si no, elimina el punto.

        Returns:
            Número de puntos eliminados.
        """
        removed = 0

        # Obtener puntos Qdrant que tengan metadata.key
        # (los puntos con tipo fact, person_role y user_preference)
        all_points = await self._query_all_points()
        if not all_points:
            return 0

        # Obtener claves KV activas
        kv_keys_raw = await self._memory._kv.search_keys("*")
        # Normalizar: quitar el prefijo agent_memory:fact:
        active_keys: set[str] = set()
        prefix = f"{PREFIX_FACT}:"
        for k in kv_keys_raw:
            if k.startswith(prefix):
                active_keys.add(k[len(prefix):])

        # Identificar huérfanos
        orphan_ids: list[str] = []
        for pt in all_points:
            meta = pt.get("metadata") or {}
            kv_key = meta.get("key", "")
            if kv_key and kv_key not in active_keys:
                orphan_ids.append(pt["id"])
                logger.debug(
                    "Huérfano detectado: key=%s id=%s", kv_key, pt["id"],
                )

        if orphan_ids:
            self._memory._vec.delete_points(orphan_ids)
            removed = len(orphan_ids)
            logger.info(
                "Sync cleanup: %d puntos huérfanos eliminados de Qdrant",
                removed,
            )

        return removed

    async def _deduplicate(self) -> dict[str, int]:
        """Fusiona puntos vectoriales duplicados (similitud > umbral).

        Estrategia: para cada tipo de memoria, agrupa puntos con texto
        similar y conserva el más reciente. La comparación usa similitud
        coseno y coincidencia de texto.

        Returns:
            Dict con ``removed`` y ``kept``.
        """
        removed = 0
        kept = 0

        all_points = await self._query_all_points()
        if not all_points:
            return {"removed": 0, "kept": 0}

        # Agrupar por tipo de memoria
        by_type: dict[str, list[dict[str, Any]]] = {}
        for pt in all_points:
            mtype = pt.get("memory_type", "unknown")
            by_type.setdefault(mtype, []).append(pt)

        for mtype, points in by_type.items():
            if len(points) < 2:
                continue

            # Ordenar por created_at descendente (más reciente primero)
            points.sort(key=lambda p: p.get("created_at", ""), reverse=True)

            to_remove: set[str] = set()

            for i, a in enumerate(points):
                if a["id"] in to_remove:
                    continue
                for j, b in enumerate(points):
                    if i >= j or b["id"] in to_remove:
                        continue
                    if self._is_near_duplicate(a, b):
                        # Conservar a (más reciente), eliminar b
                        to_remove.add(b["id"])

            if to_remove:
                self._memory._vec.delete_points(list(to_remove))
                removed += len(to_remove)
                kept += len(points) - len(to_remove)
                logger.debug(
                    "Dedup %s: %d conservados, %d eliminados",
                    mtype, kept, removed,
                )

        return {"removed": removed, "kept": kept}

    async def _compress_episodes(self) -> int:
        """Comprime episodios antiguos en resúmenes mensuales.

        Episodios con más de ``episode_age_days`` días se agrupan por
        mes y se reemplazan por un único punto resumen. El texto
        original se condensa en una línea por episodio.

        Returns:
            Número de episodios originales reemplazados.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._episode_age_days)
        compressed = 0

        # Buscar episodios viejos en Qdrant
        old_episodes = await self._query_points_by_type(
            "episode", older_than=cutoff,
        )
        if not old_episodes:
            return 0

        # Agrupar por mes
        by_month: dict[str, list[dict[str, Any]]] = {}
        for ep in old_episodes:
            created = ep.get("created_at", "")
            month_key = created[:7]  # "2026-03"
            by_month.setdefault(month_key, []).append(ep)

        for month, episodes in by_month.items():
            if len(episodes) < 2:
                continue  # no vale la pena comprimir un solo episodio

            # Construir resumen mensual
            lines: list[str] = []
            session_ids: list[str] = []
            for ep in episodes:
                meta = ep.get("metadata") or {}
                text = ep.get("text", "")[:120]
                sid = meta.get("session_id", "?")
                lines.append(f"  - [{sid}] {text}")
                session_ids.append(sid)

            summary_text = (
                f"[Resumen mensual {month}] "
                f"{len(episodes)} interacciones:\n" +
                "\n".join(lines)
            )

            # Crear nuevo punto resumen
            await self._memory._vec.store(
                text=summary_text,
                memory_type="episode",
                metadata={
                    "month": month,
                    "n_episodes": len(episodes),
                    "session_ids": session_ids,
                    "compressed": True,
                },
                ttl_days=None,  # permanente
            )

            # Eliminar episodios originales
            old_ids = [ep["id"] for ep in episodes]
            self._memory._vec.delete_points(old_ids)
            compressed += len(old_ids)
            logger.info(
                "Episodios comprimidos: %d episodios → 1 resumen mensual para %s",
                len(episodes), month,
            )

        return compressed

    async def _prune_low_relevance(self) -> int:
        """Elimina puntos vectoriales de baja relevancia.

        Un punto se considera de baja relevancia si:
        - Es tipo ``fact`` o ``person_role`` (no episodios ni decisiones)
        - No tiene ``last_recalled`` o su ``last_recalled`` supera los
          ``low_relevance_days`` días
        - Existe otro punto más reciente con la misma clave semántica (``metadata.key``)

        Returns:
            Número de puntos eliminados.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._low_relevance_days)
        pruned = 0

        for mtype in ("fact", "person_role", "user_preference"):
            points = await self._query_points_by_type(mtype)
            if not points:
                continue

            # Agrupar por clave semántica (metadata.key)
            by_key: dict[str, list[dict[str, Any]]] = {}
            for pt in points:
                meta = pt.get("metadata") or {}
                key = meta.get("key", "")
                if key:
                    by_key.setdefault(key, []).append(pt)

            to_remove: list[str] = []
            for key, group in by_key.items():
                if len(group) < 2:
                    continue  # solo una entrada para esta clave → mantener
                # Ordenar por created_at descendente
                group.sort(key=lambda p: p.get("created_at", ""), reverse=True)
                # Conservar la más reciente, marcar las demás
                for pt in group[1:]:
                    last_recalled = pt.get("metadata", {}).get("last_recalled")
                    if last_recalled:
                        try:
                            lr = datetime.fromisoformat(last_recalled)
                            if lr < cutoff:
                                to_remove.append(pt["id"])
                        except (ValueError, TypeError):
                            to_remove.append(pt["id"])
                    else:
                        # Sin last_recalled → nunca se ha recuperado
                        to_remove.append(pt["id"])

            if to_remove:
                self._memory._vec.delete_points(to_remove)
                pruned += len(to_remove)
                logger.debug(
                    "Low-relevance prune %s: %d puntos eliminados",
                    mtype, len(to_remove),
                )

        return pruned

    # ── Helpers de consulta Qdrant ─────────────────────────────────────

    async def _query_all_points(self) -> list[dict[str, Any]]:
        """Recupera TODOS los puntos de la colección de memoria.

        Returns:
            Lista plana de todos los puntos (sin paginación).
        """
        result = self._memory._vec.get_all_points()
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _query_points_by_type(
        self,
        memory_type: str,
        older_than: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera puntos de un tipo específico, opcionalmente filtrando por edad.

        Args:
            memory_type: Tipo de memoria a filtrar.
            older_than: Si se proporciona, solo devuelve puntos con
                        ``created_at`` anterior a esta fecha.

        Returns:
            Lista de puntos filtrados.
        """
        result = self._memory._vec.get_points_by_type(
            memory_type, older_than=older_than,
        )
        if asyncio.iscoroutine(result):
            result = await result
        return result

    # ── Helpers de comparación ─────────────────────────────────────────

    @staticmethod
    def _is_near_duplicate(
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> bool:
        """Determina si dos puntos vectoriales son duplicados cercanos.

        Criterios:
        1. Mismo tipo de memoria.
        2. Misma clave semántica (metadata.key) si existe.
        3. Alta similitud coseno (> ``dedup_similarity``) O
           textos casi idénticos normalizados.

        Args:
            a: Primer punto (más reciente).
            b: Segundo punto (más antiguo).

        Returns:
            True si son duplicados cercanos.
        """
        if a.get("memory_type") != b.get("memory_type"):
            return False

        meta_a = a.get("metadata") or {}
        meta_b = b.get("metadata") or {}
        key_a = meta_a.get("key", "")
        key_b = meta_b.get("key", "")
        if key_a and key_b and key_a == key_b:
            return True  # misma clave → duplicado semántico

        # Comparación de texto normalizado
        text_a = _normalize_text(a.get("text", ""))
        text_b = _normalize_text(b.get("text", ""))

        if not text_a or not text_b:
            return False

        # Si el texto es casi idéntico, es duplicado
        if text_a == text_b:
            return True

        # Verificar si score de similitud está disponible
        score = a.get("score") or b.get("score")
        if score is not None and score >= _DEFAULT_DEDUP_SIMILARITY:
            return True

        return False

    # ── Auditoría ──────────────────────────────────────────────────────

    def _audit(self, report: ConsolidationReport) -> None:
        """Registra el resultado de la consolidación en el audit log."""
        entry = {
            "timestamp": report.timestamp,
            "event": "consolidation",
            "thread_id": "__system__",
            "user_id": None,
            "proposal": None,
            "user_message": None,
            "result": {
                "synced_orphans": report.synced_orphans,
                "dedup_removed": report.dedup_removed,
                "dedup_kept": report.dedup_kept,
                "episodes_compressed": report.episodes_compressed,
                "low_relevance_pruned": report.low_relevance_pruned,
                "errors": report.errors,
                "duration_ms": round(report.duration_ms, 1),
            },
        }
        try:
            _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("No se pudo escribir auditoría de consolidación: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    """Normaliza texto para comparación de duplicados.

    - Elimina espacios extra.
    - Convierte a minúsculas.
    - Trunca a _MAX_DEDUP_TEXT_LENGTH caracteres.
    """
    import re
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text[:_MAX_DEDUP_TEXT_LENGTH]


def _now_iso() -> str:
    """Retorna timestamp ISO en UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
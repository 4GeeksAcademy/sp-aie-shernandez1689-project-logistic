"""Almacén clave-valor para memoria de agentes.

Usa Redis como backend. Cada hecho se almacena como una clave con prefijo
``agent_memory:`` y, cuando aplica, un TTL para que la memoria episódica
no se acumule indefinidamente.

El store valida que las claves y valores cumplan las reglas de negocio
de TrackFlow antes de escribir.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ── Prefijos de clave ──────────────────────────────────────────────────

PREFIX_FACT = "agent_memory:fact"
PREFIX_EPISODE = "agent_memory:episode"
PREFIX_USER_PREFS = "agent_memory:user"
PREFIX_SESSION = "agent_memory:session"

# ── TTL por defecto ────────────────────────────────────────────────────

DEFAULT_EPISODE_TTL = timedelta(days=30)       # episodios expiran a los 30 días
DEFAULT_FACT_TTL: timedelta | None = None      # hechos aprobados: sin expiración
DEFAULT_PREFS_TTL = timedelta(days=90)          # preferencias de usuario: 90 días

# ── Validación de hechos memorizables ─────────────────────────────────

# Patrones de clave PERMITIDOS para escritura.
_ALLOWED_KEY_PATTERNS: list[re.Pattern] = [
    re.compile(r"^person:role:[a-zA-Z0-9_-]+$"),              # person:role:ana
    re.compile(r"^user:[a-zA-Z0-9_-]+:pref:[a-zA-Z0-9_-]+$"), # user:uuid:pref:lang
    re.compile(r"^user:[a-zA-Z0-9_-]+:approved:[a-zA-Z0-9_-]+$"), # user:uuid:approved:threshold
    re.compile(r"^session:[a-zA-Z0-9_-]+:summary$"),           # session:uuid:summary
    re.compile(r"^fact:[a-zA-Z0-9_-]+$"),                      # fact:business_rule_xyz
    re.compile(r"^decision:[a-zA-Z0-9_-]+$"),                  # decision:threshold_low_stock
]

# Palabras/claves TERMINANTEMENTE PROHIBIDAS en valores (datos personales).
_FORBIDDEN_VALUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),   # posible tarjeta/número largo
    re.compile(r"\b(?:password|secret|token|credential)\s*[:=]\s*\S+", re.IGNORECASE),
]

# Valores que NUNCA deben memorizarse.
_FORBIDDEN_VALUE_SUBSTRINGS: list[str] = [
    "password",
    "contraseña",
    "credit card",
    "tarjeta",
    "ssn",
    "social security",
]


def _validate_key(key: str) -> None:
    """Lanza ValueError si la clave no sigue los patrones permitidos."""
    for pattern in _ALLOWED_KEY_PATTERNS:
        if pattern.fullmatch(key):
            return
    raise ValueError(
        f"Clave '{key}' no sigue ningún patrón de clave permitido. "
        f"Patrones válidos: {[p.pattern for p in _ALLOWED_KEY_PATTERNS]}"
    )


def _validate_value(value: str) -> None:
    """Lanza ValueError si el valor contiene datos prohibidos."""
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        if pattern.search(value):
            raise ValueError(
                "El valor contiene datos prohibidos (email, número de tarjeta, "
                "credencial). No se puede memorizar."
            )
    lower = value.lower()
    for substr in _FORBIDDEN_VALUE_SUBSTRINGS:
        if substr in lower:
            raise ValueError(
                f"El valor contiene contenido prohibido ('{substr}'). "
                f"No se puede memorizar."
            )


# ── Store ─────────────────────────────────────────────────────────────


class MemoryKVStore:
    """Almacén clave-valor para memoria de agente, respaldado por Redis.

    Toda escritura pasa por validación de clave y valor contra las reglas
    de negocio de TrackFlow. El agente llama explícitamente a write/read;
    nunca se inyecta estado en el prompt del sistema.

    Uso
    ---
        store = MemoryKVStore()
        await store.write("person:role:ana", "Head of Warehouse Operations")
        role = await store.read("person:role:ana")
        await store.write("user:alice:pref:language", "es")
        prefs = await store.read_user_prefs("alice")
    """

    def __init__(
        self,
        redis_url: str | None = None,
        episode_ttl: timedelta = DEFAULT_EPISODE_TTL,
        fact_ttl: timedelta | None = DEFAULT_FACT_TTL,
        prefs_ttl: timedelta = DEFAULT_PREFS_TTL,
    ) -> None:
        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = aioredis.from_url(url, decode_responses=True)
        self._episode_ttl = episode_ttl
        self._fact_ttl = fact_ttl
        self._prefs_ttl = prefs_ttl

    async def _redis_key(self, suffix: str) -> str:
        return f"{PREFIX_FACT}:{suffix}"

    async def write(self, key: str, value: Any, ttl: timedelta | None = None) -> None:
        """Escribe un hecho en memoria.

        Args:
            key: Clave del hecho (ej. ``person:role:ana``). Se valida contra
                 los patrones permitidos.
            value: Valor a almacenar (dict, str, int, etc.). Se serializa a JSON
                   y se valida contra contenido prohibido.
            ttl: Tiempo de vida. Si es None, usa el TTL por defecto del tipo
                 de hecho (fact→permanente, episode→30d, user:pref→90d).

        Raises:
            ValueError: si la clave o el valor violan las reglas de negocio.
        """
        _validate_key(key)
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        _validate_value(serialized)

        rkey = await self._redis_key(key)
        effective_ttl = ttl
        if effective_ttl is None:
            if key.startswith("session:"):
                effective_ttl = self._episode_ttl
            elif key.startswith("user:") and ":pref:" in key:
                effective_ttl = self._prefs_ttl
            elif key.startswith("episode:"):
                effective_ttl = self._episode_ttl

        if effective_ttl is not None:
            await self._redis.setex(rkey, int(effective_ttl.total_seconds()), serialized)
        else:
            await self._redis.set(rkey, serialized)

        logger.debug("Memoria KV escrita: %s (%s)", key, "con TTL" if effective_ttl else "permanente")

    async def read(self, key: str) -> Any | None:
        """Lee un hecho de memoria.

        Returns:
            El valor deserializado, o None si no existe.
        """
        rkey = await self._redis_key(key)
        raw = await self._redis.get(rkey)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def delete(self, key: str) -> bool:
        """Elimina un hecho de memoria.

        Returns:
            True si existía y se eliminó, False si no existía.
        """
        rkey = await self._redis_key(key)
        deleted = await self._redis.delete(rkey)
        if deleted:
            logger.debug("Memoria KV eliminada: %s", key)
        return deleted > 0

    async def search_keys(self, pattern: str) -> list[str]:
        """Busca claves que coincidan con un patrón glob.

        Args:
            pattern: Patrón glob (ej. ``person:role:*``, ``user:*:pref:*``).

        Returns:
            Lista de claves completas (con prefijo ``agent_memory:fact:``).
        """
        full_pattern = f"{PREFIX_FACT}:{pattern}"
        cursor = 0
        keys: list[str] = []
        while True:
            cursor, batch = await self._redis.scan(
                cursor=cursor, match=full_pattern, count=100
            )
            keys.extend(batch)
            if cursor == 0:
                break
        return keys

    async def read_user_prefs(self, user_id: str) -> dict[str, Any]:
        """Lee todas las preferencias de un usuario.

        Returns:
            Dict con clave→valor de las preferencias del usuario.
        """
        keys = await self.search_keys(f"user:{user_id}:pref:*")
        prefs: dict[str, Any] = {}
        for full_key in keys:
            # extraer el sufijo después de agent_memory:fact:
            suffix = full_key.replace(f"{PREFIX_FACT}:", "", 1)
            val = await self.read(suffix)
            if val is not None:
                pref_name = suffix.split(":")[-1]
                prefs[pref_name] = val
        return prefs

    async def write_user_pref(self, user_id: str, pref_name: str, value: Any) -> None:
        """Escribe una preferencia de usuario.

        Atajo para ``write(f"user:{user_id}:pref:{pref_name}", value)``.
        """
        await self.write(f"user:{user_id}:pref:{pref_name}", value)

    async def write_episode_summary(
        self, session_id: str, summary: dict[str, Any]
    ) -> None:
        """Escribe el resumen de una sesión/episodio.

        El resumen incluye: qué se preguntó, qué fuentes se usaron (RAG/tools),
        qué se respondió, y cualquier hecho aprobado durante la interacción.
        """
        await self.write(f"session:{session_id}:summary", summary)

    async def read_episode_summary(self, session_id: str) -> dict[str, Any] | None:
        """Lee el resumen de una sesión."""
        return await self.read(f"session:{session_id}:summary")

    async def close(self) -> None:
        """Cierra la conexión Redis."""
        await self._redis.aclose()

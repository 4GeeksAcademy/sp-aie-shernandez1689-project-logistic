"""In-memory TTL cache for endpoints with stable, shared data.

Estrategia de cacheo
--------------------
La API sirve datos compartidos entre todos los usuarios autenticados
(incidencias, proveedores). No hay datos personalizados por usuario,
por lo que una clave de caché compartida es segura.

Tipos de caché:
- Cache con TTL: los datos expiran automáticamente tras un tiempo.
  Apropiado para endpoints de solo lectura donde la frecuencia de
  consulta >> frecuencia de escritura.
- Invalidación explícita: al escribir (crear, actualizar, eliminar),
  se purgan las claves de caché afectadas para asegurar consistencia
  inmediata sin esperar al TTL.

Uso:
    from cache import incident_cache, supplier_cache

    @router.get("/api/incidents")
    def list_incidents(...):
        cached = incident_cache.get("list:status=open")
        if cached: return cached
        ...
        incident_cache.set("list:status=open", result, ttl=30)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any


class TTLCache:
    """Caché en memoria con TTL, thread-safe e invalidable por prefijo.

    Cada entrada almacena (expira_en, valor). El TTL por defecto se define
    por instancia; también se puede pasar un TTL personalizado en cada set.
    La invalidación por prefijo permite limpiar grupos de claves relacionados
    (ej. todos los listados de incidencias) con una sola llamada.
    """

    def __init__(self, default_ttl: float, name: str = ""):
        self._default_ttl = default_ttl
        self._name = name
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    # ── Acceso básico ──────────────────────────────────────────────

    def get(self, key: str) -> tuple[bool, Any]:
        """(hit, value). hit=False si no existe o expiró."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return False, None
            return True, value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            self._store[key] = (expires_at, value)

    # ── Invalidación ───────────────────────────────────────────────

    def invalidate(self, key: str) -> None:
        """Invalida una clave exacta."""
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Invalida todas las claves que comienzan con *prefix*.

        Ejemplo: incident_cache.invalidate_prefix("list:") limpia
        "list:", "list:status=open", "list:status=resolved", etc.
        """
        with self._lock:
            self._store = {
                k: v
                for k, v in self._store.items()
                if not k.startswith(prefix)
            }

    def invalidate_all(self) -> None:
        with self._lock:
            self._store.clear()

    # ── Decorador para funciones síncronas ─────────────────────────

    def cached(self, ttl: float | None = None):
        """Decorador: cachea el resultado con TTL opcional.

        La clave se genera automáticamente a partir del nombre de la
        función y sus argumentos posicionales/nombrados.

        Útil para envolver lógica existente sin cambiar su firma.
        Las funciones decoradas NO DEBEN tener efectos secundarios.
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                key_parts = [func.__qualname__]
                key_parts.extend(str(a) for a in args)
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = "|".join(key_parts)

                hit, value = self.get(cache_key)
                if hit:
                    return value

                result = func(*args, **kwargs)
                self.set(cache_key, result, ttl)
                return result
            return wrapper
        return decorator


# ── Instancias de caché ────────────────────────────────────────────

# Incidencias: TTL 60 s porque se consultan en dashboard y la tabla
# cambia con poca frecuencia (solo creación o cambio de estado).
incident_cache = TTLCache(default_ttl=60.0, name="incidents")

# Proveedores: TTL 120 s porque los datos cambian aún con menos
# frecuencia (solo ediciones manuales de tarifa/estado).
supplier_cache = TTLCache(default_ttl=120.0, name="suppliers")

# Claves prefijadas para invalidación selectiva
CACHE_KEY_INCIDENTS_LIST = "incidents:list:"
CACHE_KEY_INCIDENTS_SUMMARY = "incidents:summary"
CACHE_KEY_SUPPLIERS_LIST = "suppliers:list:"
CACHE_KEY_SUPPLIERS_DETAIL = "suppliers:detail:"
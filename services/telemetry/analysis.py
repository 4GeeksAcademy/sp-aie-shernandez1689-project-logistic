"""Pipeline de análisis operacional de telemetría — Fase 1.

Cada función de métrica sigue la fórmula del reporte técnico:

    cargar (SQL) → refinar (Pandas) → convertir tipos → agrupar → agregar → servir

Reglas del proyecto (tele-report.md):
- El rango de ``timestamp`` y el filtro de ``event_type`` se aplican **en SQL**.
- Las dimensiones dentro de ``tags`` (warehouse, endpoint, etc.) y los
  flags derivados (``is_error``, tasas) se construyen **en Pandas**.
- Los timestamps se convierten con ``pd.to_datetime(..., utc=True)`` antes
  de cualquier ``groupby()`` temporal.
- No se usan loops: solo operaciones de Pandas (``.groupby()``, ``.agg()``,
  ``.count()``, ``.sum()``, ``.mean()``).
- Cada función es pura e independiente: con los mismos parámetros devuelve
  el mismo resultado; no tiene efectos secundarios.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text
from sqlmodel import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Referencias del catálogo de eventos (docs/telemetry/telemetry-plan.md)
# ---------------------------------------------------------------------------

# Un evento cuenta como fallo técnico si su ``level`` está en este conjunto.
# El nivel se deriva en la ingesta (routes/telemetry.py::_derive_level):
#   api_response_error_recorded 5xx → 'error'
#   api_response_error_recorded 4xx → 'warn'
#   login_failed, direct_stock_edit_rejected, stock_threshold_triggered → 'warn'
ERROR_LEVELS = frozenset({"error", "warn"})

# Eventos de autenticación (actividad adicional del tele-report.md)
AUTH_EVENT_TYPES = ("login_failed", "login_succeeded")


# ---------------------------------------------------------------------------
# Receta: convertir filas SQL en DataFrame con timestamp tipado
# ---------------------------------------------------------------------------


def _rows_to_dataframe(rows: list[Any]) -> pd.DataFrame:
    """Convierte filas SQL en un DataFrame con timestamps tipados UTC.

    Los timestamps llegan como ``str`` desde la base de datos; se convierten
    con ``pd.to_datetime(..., utc=True)`` **antes** de cualquier ``groupby()``
    temporal — la fuente clásica de agrupaciones incorrectas silenciosas.
    """
    if not rows:
        return pd.DataFrame({"timestamp": pd.Series(dtype="datetime64[ns, UTC]")})
    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _extract_tags(df: pd.DataFrame, key: str) -> pd.Series:
    """Extrae la dimensión ``key`` de la columna ``tags`` (JSONB).

    Devuelve una serie alineada con el índice de ``df``; el llamador descarta
    las filas con valor nulo para segmentar **todos** los valores presentes,
    no prefiltrados.
    """
    if "tags" not in df.columns:
        return pd.Series([None] * len(df), index=df.index, name=key)
    return df["tags"].apply(
        lambda tags: tags.get(key) if isinstance(tags, dict) else None
    )


def _to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Sirve el resultado como lista de dicts JSON-serializable."""
    return df.reset_index().to_dict(orient="records")


# ---------------------------------------------------------------------------
# Métrica 1 — Volumen de eventos por día
# ---------------------------------------------------------------------------


def events_per_day(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Volumen total de eventos por día en la ventana.

    Pregunta operacional: ¿qué volumen de telemetría entra cada día y hay
    picos o caídas? Es el pulso básico del sistema.

    SQL: SELECT timestamp FROM telemetry_events WHERE start <= ts < end.
    """
    stmt = text(
        """
        SELECT timestamp
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
        """
    )
    rows = session.execute(stmt, {"start_date": start_date, "end_date": end_date}).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["date"] = df["timestamp"].dt.date
    result = (
        df.groupby("date", as_index=False)
        .agg(events=("timestamp", "count"))
        .sort_values("date")
    )
    result["date"] = result["date"].astype(str)
    return _to_records(result)


# ---------------------------------------------------------------------------
# Métrica 2 — Volumen por tipo de evento y por día
# ---------------------------------------------------------------------------


def events_per_type_per_day(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Volumen de eventos por tipo y por día.

    Pregunta operacional: ¿qué tipos de evento dominan el tráfico y cómo
    evolucionan en el tiempo? Sin esta segmentación, un pico global no dice
    nada sobre su origen.

    SQL: SELECT timestamp, event_type FROM telemetry_events WHERE ventana.
    """
    stmt = text(
        """
        SELECT timestamp, event_type
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
        """
    )
    rows = session.execute(
        stmt, {"start_date": start_date, "end_date": end_date}
    ).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["date"] = df["timestamp"].dt.date
    result = (
        df.groupby(["date", "event_type"], as_index=False)
        .agg(events=("event_type", "count"))
        .sort_values(["date", "event_type"])
    )
    result["date"] = result["date"].astype(str)
    return _to_records(result)


# ---------------------------------------------------------------------------
# Métrica 3 — Tasa de error por día
# ---------------------------------------------------------------------------


def error_rate_daily(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Tasa de eventos técnicos error/warn sobre el total, por día.

    Pregunta operacional: ¿qué fracción de la telemetría indica un problema
    (5xx, 4xx, login fallido) cada día? Es el indicador de salud principal;
    un día con picos de degradación se detecta al instante.

    ``is_error`` se deriva en Pandas a partir del ``level`` ya derivado en la
    ingesta (routes/telemetry.py::_derive_level), evitando aplicar la regla de
    negocio de nuevo en cada métrica.
    """
    stmt = text(
        """
        SELECT timestamp, event_type, level
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
          AND event_type IN :error_event_types
        """
    ).bindparams(bindparam("error_event_types", expanding=True))
    rows = session.execute(
        stmt,
        {
            "start_date": start_date,
            "end_date": end_date,
            "error_event_types": ("api_response_error_recorded",),
        },
    ).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["is_error"] = df["level"].isin(ERROR_LEVELS)
    df["date"] = df["timestamp"].dt.date

    total = df.groupby("date", as_index=False).agg(total=("event_type", "count"))
    errors = (
        df[df["is_error"]]
        .groupby("date", as_index=False)
        .agg(errors=("is_error", "sum"))
    )
    merged = total.merge(errors, on="date", how="left").fillna({"errors": 0})
    merged["error_rate"] = (merged["errors"] / merged["total"]).round(4)
    merged["date"] = merged["date"].astype(str)
    return _to_records(merged.sort_values("date"))


# ---------------------------------------------------------------------------
# Métrica 4 — Distribución de la tasa de error por tipo de evento
# ---------------------------------------------------------------------------


def error_rate_by_type(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Tasa de error por tipo de evento (solo los que pueden fallar).

    Pregunta operacional: ¿qué tipo de evento concentra más fallos? Un
    componente con tasa alta es más urgente que uno con volumen alto pero
    tasa baja: ayuda a priorizar dónde invertir tiempo de corrección.
    """
    stmt = text(
        """
        SELECT event_type, level
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
          AND event_type IN :error_event_types
        """
    ).bindparams(bindparam("error_event_types", expanding=True))
    rows = session.execute(
        stmt,
        {
            "start_date": start_date,
            "end_date": end_date,
            "error_event_types": ("api_response_error_recorded",),
        },
    ).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["is_error"] = df["level"].isin(ERROR_LEVELS)
    total = df.groupby("event_type", as_index=False).agg(total=("event_type", "count"))
    errors = (
        df[df["is_error"]]
        .groupby("event_type", as_index=False)
        .agg(errors=("is_error", "sum"))
    )
    merged = total.merge(errors, on="event_type", how="left").fillna({"errors": 0})
    merged["error_rate"] = (merged["errors"] / merged["total"]).round(4)
    merged = merged.sort_values("error_rate", ascending=False)
    return _to_records(merged)


# ---------------------------------------------------------------------------
# Métrica 5 — Fallos de API (5xx/4xx) por endpoint
# ---------------------------------------------------------------------------


def api_errors_by_endpoint(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Errores de API (4xx/5xx) agregados por endpoint.

    Pregunta operacional: ¿qué endpoints fallan más? La hipótesis del
    catálogo para ``api_response_error_recorded`` es "conocer la tasa por
    endpoint para priorizar correcciones". La dimensión ``endpoint`` vive en
    ``tags`` → se extrae en Pandas descartando filas sin valor.
    """
    stmt = text(
        """
        SELECT timestamp, level, tags
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
          AND event_type = 'api_response_error_recorded'
        """
    )
    rows = session.execute(
        stmt, {"start_date": start_date, "end_date": end_date}
    ).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["endpoint"] = _extract_tags(df, "endpoint")
    df = df[df["endpoint"].notna()]
    if df.empty:
        return []

    df["is_5xx"] = (df["level"] == "error").astype(int)
    summary = (
        df.groupby("endpoint", as_index=False)
        .agg(errors=("endpoint", "count"), server_errors=("is_5xx", "sum"))
        .sort_values("errors", ascending=False)
    )
    summary["server_errors"] = summary["server_errors"].astype(int)
    return _to_records(summary)


# ---------------------------------------------------------------------------
# Métrica 6 (actividad adicional) — Tasa diaria de fallos de login
# ---------------------------------------------------------------------------


def auth_failure_rate_daily(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Tasa diaria de fallos de login por día.

    Pregunta operacional/seguridad: de todos los intentos de login del día,
    ¿qué fracción falló? Detecta picos anómalos (fuerza bruta, problema de
    credenciales) tal y como pide Andrés (CTO).

    Carga ambos tipos de evento con ``event_type IN (...)` en una sola
    consulta SQL, calcula la razón en Pandas.
    """
    stmt = text(
        """
        SELECT timestamp, event_type
        FROM telemetry_events
        WHERE timestamp >= :start_date AND timestamp < :end_date
          AND event_type IN :auth_event_types
        """
    ).bindparams(bindparam("auth_event_types", expanding=True))
    rows = session.execute(
        stmt,
        {
            "start_date": start_date,
            "end_date": end_date,
            "auth_event_types": AUTH_EVENT_TYPES,
        },
    ).all()
    df = _rows_to_dataframe(rows)
    if df.empty:
        return []

    df["is_failure"] = (df["event_type"] == "login_failed").astype(int)
    df["date"] = df["timestamp"].dt.date

    attempts = df.groupby("date", as_index=False).agg(
        attempts=("event_type", "count"),
        failures=("is_failure", "sum"),
    )
    attempts["failure_rate"] = (attempts["failures"] / attempts["attempts"]).round(4)
    attempts["date"] = attempts["date"].astype(str)
    return _to_records(attempts.sort_values("date"))


# ---------------------------------------------------------------------------
# Función agregadora que ejecuta todas las métricas para el endpoint
# ---------------------------------------------------------------------------


def build_telemetry_report(
    session: Session,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Ejecuta todo el pipeline y devuelve el JSON completo del reporte.

    El endpoint llama a esta función **fuera del request de cálculo** con la
    ventana resuelta una sola vez; las métricas se calculan de una vez y el
    resultado se serve con cache (TTL 60 s en el endpoint).
    """
    return {
        "events_per_day": events_per_day(session, start_date, end_date),
        "events_per_type_per_day": events_per_type_per_day(
            session, start_date, end_date
        ),
        "error_rate_daily": error_rate_daily(session, start_date, end_date),
        "error_rate_by_type": error_rate_by_type(session, start_date, end_date),
        "api_errors_by_endpoint": api_errors_by_endpoint(
            session, start_date, end_date
        ),
        "auth_failure_rate": auth_failure_rate_daily(session, start_date, end_date),
    }
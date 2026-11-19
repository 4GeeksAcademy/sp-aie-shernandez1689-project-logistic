"""Condiciones comerciales estándar que pueden citar las propuestas (Parte 2).

Única fuente de cifras de TrackFlow para el generador: la RFP aporta los datos
del cliente (handoff de la Parte 1) y este catálogo los de TrackFlow. Nada
fuera de aquí puede aparecer como precio, plazo o porcentaje en un borrador.

Cada término lleva su ``source``:

- ``kb:<documento>`` — copiado de ``docs/company-knowledge-base/`` (misma base
  que indexa el RAG del Hito 7).
- ``param:<responsable>`` — parámetro comercial que la KB no recoge pero que
  los lineamientos del BRIEFING §5 obligan a incluir (SLA a tiempo en %, tabla
  de descuentos, plazo de devoluciones ≥ 48 h). Lo valida el responsable del
  departamento en la aprobación de la Parte 3.

Lo que no está ni en la KB ni aquí (p. ej. tarifas de almacenaje en USD o el
costo base por envío según destinos) se escribe como "a cotizar por
<responsable>", nunca con una cifra.
"""

from __future__ import annotations

from typing import Any

# Transportistas por mercado (kb:carrier-coverage). Se pueden nombrar como
# disponibles por destino; sus tarifas negociadas nunca (BRIEFING §5).
CARRIERS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "EUR": (
        ("Correos", "Nacional (peninsular + islas)", "Estándar y certificado"),
        ("SEUR", "Peninsular", "Express y same-day"),
        ("MRW", "Peninsular + Canarias", "Estándar y express"),
        ("DHL Spain", "Peninsular", "Internacional y express"),
    ),
    "USD": (
        ("UPS", "Continental US", "Estándar y express"),
        ("FedEx", "Continental US + Alaska", "Estándar y express"),
        ("USPS", "Continental US", "Estándar (económico)"),
        ("DHL Express", "Internacional", "Express internacional"),
    ),
}

CARRIER_NAMES: tuple[str, ...] = tuple(
    sorted({name.split()[0] for rows in CARRIERS.values() for name, _, _ in rows})
)

# Centro que opera cada mercado.
SITES: dict[str, str] = {"EUR": "Zaragoza", "USD": "Los Angeles"}

# SLA de cumplimiento a tiempo por departamento (BRIEFING §5: toda propuesta
# indica el % comprometido). La KB no fija el porcentaje → parámetro.
ON_TIME_SLA: dict[str, dict[str, Any]] = {
    "warehouse": {
        "metric": "pedidos preparados y despachados en el día de corte",
        "pct": 99,
        "source": "param:Ana Whitfield",
    },
    "lastmile": {
        "metric": "entregas a tiempo dentro del plazo del servicio contratado",
        "pct": 95,
        "source": "param:Carlos Vega",
    },
    "reverse": {
        "metric": "devoluciones procesadas dentro del plazo comprometido",
        "pct": 95,
        "source": "param:Sofía Ramos",
    },
}

# Excepción por picos de demanda (kb:sla-delivery): el SLA no se promete en
# fechas de alta demanda declaradas.
PEAK_DEMAND_NOTE = (
    "En picos de demanda declarados (Black Friday, Cyber Monday, rebajas de "
    "enero, Prime Day) los plazos pueden extenderse. TrackFlow lo notifica con "
    "7 días de antelación."
)

# Plazos de entrega por mercado (kb:sla-delivery).
DELIVERY_TIMES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "EUR": (
        ("Estándar", "2–3 días laborables", "Incluido en la tarifa base"),
        ("Express", "24 horas", "Suplemento de 3,50 €"),
        ("Same-day", "Mismo día (pedidos antes de 12:00)", "Suplemento de 7,00 €"),
    ),
    "USD": (
        ("Estándar", "3–5 días laborables", "Incluido en la tarifa base"),
        ("Express", "2 días laborables", "Suplemento de 5,50 USD"),
        ("Same-day", "Mismo día (solo zonas metropolitanas)", "Suplemento de 12,00 USD"),
    ),
}

# Tarifas de almacenaje y picking (kb:storage-pricing). Solo existen en EUR.
WAREHOUSE_PRICES: dict[str, tuple[tuple[str, str], ...]] = {
    "EUR": (
        ("Almacenaje palet estándar", "4,50 € por palet/día"),
        ("Almacenaje caja pequeña (60×40×30 cm)", "0,80 € por unidad/día"),
        ("Picking estándar", "0,35 € por línea"),
        ("Picking por lote", "0,25 € por línea"),
        ("Etiquetado personalizado", "0,15 € por unidad"),
    ),
}
STORAGE_GRACE_DAYS = 30  # kb:storage-pricing (período de carencia)

# Tablas de descuento por volumen (BRIEFING §5: obligatorias).
VOLUME_DISCOUNTS: dict[str, dict[str, Any]] = {
    "warehouse": {
        "unit": "Volumen mensual (palets)",
        "tiers": (("100–499 palets", "5%"), ("500–999 palets", "10%"), ("1.000+ palets", "15%")),
        "source": "kb:storage-pricing",
    },
    "lastmile": {
        "unit": "Volumen mensual (envíos)",
        "tiers": (("5.000–9.999 envíos", "3%"), ("10.000–24.999 envíos", "6%"), ("25.000+ envíos", "10%")),
        "source": "param:Carlos Vega",
    },
    "reverse": {
        "unit": "Volumen mensual (devoluciones)",
        "tiers": (("500–1.999 devoluciones", "3%"), ("2.000+ devoluciones", "6%")),
        "source": "param:Sofía Ramos",
    },
}
# Hito 7 §6 + kb:storage-pricing / kb:returns-policy.
DISCOUNT_APPROVAL_NOTE = (
    "Cualquier descuento fuera de esta tabla requiere aprobación de Miguel "
    "Torres (Dirección Comercial)."
)

# Devoluciones (kb:returns-policy + parámetro de procesamiento ≥ 48 h, §5).
RETURNS_PROCESSING_HOURS = 72  # param:Sofía Ramos — nunca < 48 (BRIEFING §5)
RETURNS_TERMS: tuple[str, ...] = (
    "Ventana de devolución estándar de 30 días naturales desde la entrega (45 en Black Friday y Navidad).",
    f"Procesamiento en almacén en {RETURNS_PROCESSING_HOURS} horas laborables desde la recepción.",
    "Reembolso al cliente final en 5–7 días laborables desde la recepción.",
)
INTERNATIONAL_RETURNS_NOTE = (
    "Las devoluciones internacionales se gestionan de forma manual con el "
    "equipo de atención al cliente, con un plazo de hasta 15 días laborables."
)

# Responsables que cotizan lo que el catálogo no cubre.
PENDING_QUOTES: dict[str, dict[str, str]] = {
    "warehouse": {
        "USD": "Tarifas de almacenaje por palet y de picking en USD: a cotizar por Ana Whitfield.",
        "onboarding": "Tiempo de onboarding: a confirmar por Ana Whitfield con el número de SKUs.",
    },
    "lastmile": {
        "base": "Costo por envío: a cotizar por Carlos Vega según destinos y peso.",
    },
    "reverse": {
        "base": "Costo por devolución procesada: a cotizar por Sofía Ramos según volumen y tipo de inspección.",
    },
}


__all__ = [
    "CARRIERS",
    "CARRIER_NAMES",
    "SITES",
    "ON_TIME_SLA",
    "PEAK_DEMAND_NOTE",
    "DELIVERY_TIMES",
    "WAREHOUSE_PRICES",
    "STORAGE_GRACE_DAYS",
    "VOLUME_DISCOUNTS",
    "DISCOUNT_APPROVAL_NOTE",
    "RETURNS_PROCESSING_HOURS",
    "RETURNS_TERMS",
    "INTERNATIONAL_RETURNS_NOTE",
    "PENDING_QUOTES",
]

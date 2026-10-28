"""Guardarraíles de dominio y anti-jailbreak para el agente TrackFlow.

Separación de autoridades (regla núcleo del system prompt)
------------------------------------------------------------
Las instrucciones de sistema viven SOLO en ``SYSTEM_PROMPT``. Todo lo que
llegue desde el usuario (pregunta, contexto recuperado, texto incrustado
en un documento) es DATO, jamás instrucción. Este módulo ofrece una capa
*defensiva, determinista y testeable* que decide ANTES de llamar al LLM:

- ``classify_bounds(question)`` -> label de guardarraíl:
    * "domain"     : dentro del dominio comercial de TrackFlow (procede).
    * "off_domain" : fuera del dominio (redirigir sin abrir el LLM).
    * "jailbreak"  : intento de manipulación del sistema/instrucciones
                     (rechazo firme + redirección, NUNCA se obedece).
    * "small_talk" : saludo cortés permitido (se reconoce y se redirige
                     suavemente al dominio).

La capa no sustituye al system prompt: es la primera línea de defensa
determinista (regex estrictas, sin LLM) para que un intento de jailbreak
ni siquiera alcance el modelo. El system prompt cubre el resto: si algo
se le escapa a esta capa, la regla 10 del prompt lo rechaza.

Diseño (deliberadamente simple y auditable):
- ``_JAILBREAK_PATTERNS``: señales explícitas de manipulación de
  instrucciones. Son FRASES EXACTAS/ESTRICTAS (word-boundary), no
  palabras sueltas, para no marcar preguntas legítimas del dominio
  (p. ej. "¿puedo aplicar un descuento?" no es jailbreak).
- ``_DOMAIN_PATTERNS``: señales del dominio comercial de TrackFlow
  (del BRIEFING: SLAs, devoluciones, transportistas, tarifas,
  almacenamiento, tickets/incidencias, inventario, informe semanal).
- ``_SMALL_TALK_PATTERNS``: saludos/despedidas mínimos permitidos.

Terminología de coincidencia de patrones que usan los tests:
- ``match_kind(question)`` -> el nombre del primer patrón que casa, o None.
- ``classify_bounds(question)`` -> GuardrailState (JAILBREAK | OFF_DOMAIN |
  DOMAIN | SMALL_TALK).

No se usa IA aquí: todo es regex pura (importable en entornos sin LLM y
totalmente unit-testeable).
"""

from __future__ import annotations

import re
from enum import Enum


# ── Estados de guardarraíl ───────────────────────────────────────────────


class GuardrailState(str, Enum):
    """Estado de guardarraíl decidido por ``classify_bounds``."""

    JAILBREAK = "jailbreak"
    OFF_DOMAIN = "off_domain"
    DOMAIN = "domain"
    SMALL_TALK = "small_talk"


# ── Señales de intento de jailbreak (manipulación del sistema) ───────────
#
# Patrones ESTRICTOS: frases que intentan reescribir el rol/reglas del
# agente, revelar/ignorar el system prompt, o preceder a instrucciones
# incrustadas. Se aplican con IGNORECASE. Variantes en español e inglés.

_JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    # Español — ignorar instrucciones/reglas del sistema.
    (
        "ignora_tus_instrucciones",
        r"ignora\s+(?:tus|las|todas\s+las|mis|tus\s+anteriores)\s+instrucciones",
    ),
    (
        "ignora_tu_prompt",
        r"ignora\s+(?:tu|el|tus|todas\s+las)\s+(?:system\s*)?prompt",
    ),
    (
        "sin_reglas",
        r"sin\s+reglas|no\s+tienes\s+reglas|sin\s+ninguna\s+regla|"
        r"olv[íi]date\s+de\s+las\s+reglas|quita\s+(?:las|todas\s+las)\s+reglas",
    ),
    (
        "cambio_de_rol",
        r"ahora\s+eres|act[uú]a\s+como\s+(?:un\s+)?(?:asistente|agente|ai|"
        r"bot|chatbot|persona|humano)|"
        r"finge\s+ser|pretende\s+ser|hazte\s+pasar\s+por",
    ),
    (
        "olvida_empresa",
        r"olvida\s+que\s+trabajas\s+para\s+trackflow|"
        r"deja\s+de\s+ser\s+el\s+asistente\s+de\s+trackflow|"
        r"no\s+trabajas\s+para\s+trackflow",
    ),
    (
        "sin_restricciones",
        r"sin\s+restricciones|sin\s+l[íi]mites|modo\s+sin\s+reglas|"
        r"modo\s+jailbreak|desbloquea\s+todas\s+las\s+reglas",
    ),
    (
        "revela_prompt",
        r"muestra\s+(?:tu|el|los)\s+(?:system\s*)?prompt|"
        r"exp[oó]n\s+(?:tu|el|los)\s+(?:system\s*)?prompt|"
        r"dime\s+(?:cu[áa]les\s+son|todas\s+las)\s+tus\s+"
        r"(?:reglas|instrucciones|prompt)",
    ),
    (
        "sobrescribe_prompt",
        r"a\s+partir\s+de\s+ahora|de\s+ahora\s+en\s+adelante|"
        r"nuevas\s+instrucciones|ignora\s+tu\s+rol|cambia\s+tu\s+rol",
    ),
    (
        "prioridad_usuario",
        r"la\s+pregunta\s+del\s+usuario\s+(?:va|tiene)\s+(?:por\s+)?delante|"
        r"el\s+usuario\s+(?:siempre\s+)?manda|lo\s+que\s+diga\s+el\s+usuario",
    ),
    (
        "falsa_delim_inline",
        r"<(?:system|instructions|ignore|rules)\s*>|"
        r"<(?:user|assistant)\s*>",
    ),
    # Inglés — bypass clásicos.
    (
        "ignore_instructions_en",
        r"ignore\s+(?:your|the|all\s+(?:of\s+)?your|previous)\s+instructions",
    ),
    (
        "no_rules_en",
        r"no\s+rules|no\s+restrictions|disregard\s+all\s+rules|"
        r"ignore\s+all\s+(?:your\s+)?rules|forget\s+(?:the|all)\s+rules",
    ),
    (
        "role_switch_en",
        r"now\s+you\s+are|act\s+as\s+(?:an?|a)\s+(?:assistant|agent|ai|bot|"
        r"chatbot|person|human)|pretend\s+to\s+be|you\s+are\s+now\s+an?|"
        r"jailbreak",
    ),
    (
        "forget_company_en",
        r"forget\s+(?:that\s+you\s+work\s+(?:for|at)\s+trackflow|"
        r"you\s+work\s+for\s+trackflow)|you\s+don'?t\s+work\s+for\s+trackflow",
    ),
    (
        "dan_mode",
        r"\bdan\s*mode\b|\bdan\b|do\s+anything\s+now|jailbreak\s+mode",
    ),
    (
        "reveal_prompt_en",
        r"show\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?prompt|"
        r"print\s+(?:your|the)\s+(?:system\s+)?prompt|"
        r"output\s+(?:your|all\s+your)\s+rules|"
        r"reveal\s+(?:your|the)\s+(?:system\s+)?prompt",
    ),
    (
        "user_authority_en",
        r"user\s+(?:message|input|instructions?)\s+(?:has|take|outrank)|"
        r"new\s+(?:top|highest)\s+priority|override\s+(?:your|all)\s+"
        r"instructions",
    ),
    (
        "fake_tagline_en",
        r"<\|?(?:system|instructions|ignore|rules)\|?>",
    ),
]

_JAILBREAK_RE = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _JAILBREAK_PATTERNS
]


# ── Señales del dominio comercial de TrackFlow (BRIEFING) ────────────────
#
# Temas permitidos: SLAs entrega, política de devoluciones, transportistas
# y cobertura, tarifas de almacenamiento/picking, aprobación de descuentos,
# tickets/incidencias, inventario/stock/SKU, informe semanal, servicios
# generales y personas/roles de la empresa.

_DOMAIN_PATTERNS: list[tuple[str, str]] = [
    (
        "sla",
        r"\bsla\b|plazo\s+de\s+entrega|tiempo\s+de\s+entrega|entrega\s+express|"
        r"same[- ]?day|entrega\s+el\s+mismo\s+d[ií]a|env[ií]o\s+est[aá]ndar|"
        r"plazos?\s+de\s+env[ií]o|tard[aá]r?[aá]?|tarda\b|en\s+cu[aá]nto\s+"
        r"(?:llega|tarda)|how\s+long|take\s+to\s+(?:arrive|deliver)|"
        r"\bdelivery\b|\bshipping\b|black\s*friday|cyber\s*monday|"
        r"prime\s*day|rebajas\s+de\s+enero|alta\s+demanda",
    ),
    (
        "devoluciones",
        r"devolucio?nes?|\breturn\b|reembolso|\brefund\b|ventana\s+de\s+"
        r"devoluci|devolver|reclamo|pol[ií]tica\s+de\s+devoluci|"
        r"defectuoso",
    ),
    (
        "transportistas",
        r"transportista|\bcarrier\b|correos|seur|mrw|dhl|ups|fedex|usps|"
        r"env[ií]o|paqueter[ií]a|cobertura\s+de\s+env[ií]o|zona\s+rural|"
        r"arago[oó]n|canarias|baleares|ceuta|melilla|respaldo",
    ),
    (
        "tarifas",
        r"tarifa|\bprecio\b|coste|cost[oa]|cu[aá]nto\s+cuesta|c[oó]mo\s+se\s+"
        r"cobra|\bpallet\b|pal[eé]|cajas?|picker|picking|tarifa\s+de\s+"
        r"almacenamiento",
    ),
    (
        "descuentos",
        r"descuento|\bdiscount\b|volumen|aprobaci[oó]n|miguel\s+torres|"
        r"direcci[oó]n\s+comercial|fuera\s+de\s+rango",
    ),
    (
        "almacenamiento",
        r"almac[ée]n|warehouse|storage|capacidad|m\s*2\b|metro\s+cuadrado|"
        r"carencia|grace|ocupaci[oó]n",
    ),
    (
        "tickets_incidencias",
        r"\bticket\b|incidencia|incident|estado\s+(?:de\s+)?mi\s+ticket",
    ),
    (
        "inventario",
        r"inventario|inventory|stock|\bsku\b|c[oó]digo\s+de\s+producto|"
        r"existencias?|unidades\s+disponibles|disponibilidad|cantidad|"
        r"umbral\s+de\s+(?:stock\s+)?alerta|alerta\s+de\s+stock|"
        r"umbral(?:es)?\s+de\s+alerta|\balert[aá]\b|\balert\b",
    ),
    (
        "informe_semanal",
        r"informe\s+semanal|weekly\s+report|desempe[ñn]o\s+semanal|"
        r"performance\s+semanal|kpis?\s+de\s+la\s+semana|mejores\s+clientes",
    ),
    (
        "servicios_empresa",
        r"trackflow|log[ií]stica|e-?commerce|env[ií]o|recogida|"
        r"warehousing|last[- ]mile|reverse\s+logistics|servicio\s+de\s+"
        r"almacenaje|qu[ée]\s+servicios",
    ),
    (
        "personas_empresa",
        r"thomas|\bana\b|miguel\s+torres|carlos|ceo|operaciones|"
        r"director\s+comercial|responsable\s+de\s+transportistas",
    ),
    (
        "cuidado_datos",
        r"cliente|client|cuenta|contract|contrato|proveedor|brand|"
        r"moda|cosm[ée]ticos|electr[oó]nica",
    ),
]

_DOMAIN_RE = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _DOMAIN_PATTERNS
]


# ── Small talk permitido (mínimo, con redirección al dominio) ────────────

_SMALL_TALK_PATTERNS: list[tuple[str, str]] = [
    (
        "saludo",
        r"^[¿¡]?\s*(hi|hello|hey|hola|buenos\s+d[ií]as|buenas\s+tardes|"
        r"buenas\s+noches)\s*[.!¡¿?]*\s*$",
    ),
    (
        "gracias",
        r"^[¿¡]?\s*(thanks|thank\s+you|gracias|muchas\s+gracias)\s*[.!]*\s*$",
    ),
    (
        "despedida",
        r"^[¿¡]?\s*(bye|goodbye|adi[oó]s|hasta\s+luego|nos\s+vemos)\s*[.!]*\s*$",
    ),
    (
        "como_estas",
        r"^[¿¡]?\s*(c[oó]mo\s+est[aá]s|how\s+are\s+you|qu[ée]\s+tal)\s*[.!¡¿?]*\s*$",
    ),
]

_SMALL_TALK_RE = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _SMALL_TALK_PATTERNS
]


# ── Frase de redirección/rechazo (regla 10 del system prompt) ────────────

REDIRECT_MESSAGE = (
    "Solo puedo ayudarte con asuntos de TrackFlow — SLAs, devoluciones, "
    "transportistas, tarifas, tickets e inventario."
)


def match_kind(question: str) -> str | None:
    """Devuelve el nombre del primer patrón que casa, o None.

    Orden de comprobación: jailbreak primero (algo que parezca dominio pero
    intente manipular el sistema debe clasificarse SIEMPRE como jailbreak),
    después dominio, después small talk.

    Parameters
    ----------
    question : str
        Pregunta/saludo del usuario en bruto.

    Returns
    -------
    str | None
        Nombre descriptivo del primer patrón detectado (p. ej.
        "ignora_tus_instrucciones") o None si no casa con nada.
    """
    text = (question or "").strip()
    if not text:
        return None
    for name, compiled in _JAILBREAK_RE:
        if compiled.search(text):
            return name
    for name, compiled in _DOMAIN_RE:
        if compiled.search(text):
            return name
    for name, compiled in _SMALL_TALK_RE:
        if compiled.search(text):
            return name
    return None


def classify_bounds(question: str) -> GuardrailState:
    """Clasifica la pregunta en uno de los estados de guardarraíl.

    Parameters
    ----------
    question : str
        Mensaje del usuario.

    Returns
    -------
    GuardrailState
        - JAILBREAK : intento de manipulación -> rechazo firme (nunca LLM).
        - OFF_DOMAIN: fuera del dominio comercial -> redirigir (sin LLM).
        - SMALL_TALK: saludo cortés permitido -> respuesta breve + redirect.
        - DOMAIN    : dentro del dominio -> proceder con el flujo normal.
    """
    text = (question or "").strip()
    if not text:
        return GuardrailState.OFF_DOMAIN
    kind = match_kind(text)
    if kind is None:
        return GuardrailState.OFF_DOMAIN
    if any(name == kind for name, _ in _JAILBREAK_PATTERNS):
        return GuardrailState.JAILBREAK
    if any(name == kind for name, _ in _DOMAIN_PATTERNS):
        return GuardrailState.DOMAIN
    return GuardrailState.SMALL_TALK


def is_jailbreak(question: str) -> bool:
    """Atajo booleano: True si la pregunta es un intento de jailbreak."""
    return classify_bounds(question) is GuardrailState.JAILBREAK


def is_off_domain(question: str) -> bool:
    """Atajo booleano: True si la pregunta está fuera del dominio (sin
    considerar el small talk permitido)."""
    return classify_bounds(question) is GuardrailState.OFF_DOMAIN


def redirect_response() -> str:
    """Devuelve la respuesta de redirección/rechazo estándar del agente."""
    return REDIRECT_MESSAGE + " ¿En qué puedo ayudarte con tus operaciones hoy?"

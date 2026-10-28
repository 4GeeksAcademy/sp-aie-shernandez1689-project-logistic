"""Extracción de texto y metadatos de un PDF de RFP.

Usa ``markitdown`` (instalado vía ``uv add markitdown``) para convertir el PDF
en texto plano/markdown. Es la única capa que toca las librerías de
extracción; el clasificador y el extractor de metadatos reciben texto, no
archivos.

Estrategia de extracción (robusta ante dependencias opcionales):
1. Intentar ``markitdown`` primero (convierte PDF → texto, e incluso HTML/tablas).
2. Si falla (p. ej. sin los extras ``[pdf]`` = pdfminer/pdfplumber), hacer
   fallback directo con ``pypdf`` sobre páginas de texto.
3. Si tampoco hay texto, devolver ``''`` sin tumbar el pipeline.

Funciones públicas
------------------
extract_text(pdf_path) -> str
    Devuelve el texto plano del PDF ('' si no se puede extraer).

extract_pdf_info(pdf_path) -> dict
    Devuelve metadatos básicos: filename, num_pages, text (best-effort).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_markitdown: Any | None = None
_pypdf_checked: bool = False


def _get_converter() -> Any:
    """Singleton perezoso de ``MarkItDown`` (None si no está disponible)."""
    global _markitdown
    if _markitdown is None:
        try:
            from markitdown import MarkItDown

            _markitdown = MarkItDown()
        except Exception as exc:  # pragma: no cover - dependencia opcional
            logger.warning("markitdown no disponible: %s", exc)
            _markitdown = False
    return _markitdown if _markitdown is not False else None


def _extract_with_markitdown(path: Path) -> str:
    converter = _get_converter()
    if converter is None:
        return ""
    try:
        result = converter.convert(str(path))
        return (result.text_content or "").strip()
    except Exception as exc:  # p. ej. faltan extras [pdf] (pdfminer/pdfplumber)
        logger.debug("markitdown no convirtió el PDF %s: %s", path, exc)
        return ""


def _extract_with_pypdf(path: Path) -> str:
    """Fallback con pypdf: extrae el texto de cada página (PDFs con capa de texto)."""
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependencia opcional
        logger.warning("pypdf no disponible: %s", exc)
        return ""

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception:  # pragma: no cover - página no legible
            continue
    return "\n".join(p for p in pages if p).strip()


def extract_text(pdf_path: str | Path) -> str:
    """Convierte un PDF a texto plano (markitdown → pypdf fallback).

    Parameters
    ----------
    pdf_path : str | Path
        Ruta al archivo PDF.

    Returns
    -------
    str
        El texto extraído. Devuelve ``''`` si no se puede extraer.
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.warning("PDF no encontrado: %s", path)
        return ""

    text = _extract_with_markitdown(path)
    if text:
        return text

    text = _extract_with_pypdf(path)
    if text:
        return text

    logger.warning("No se pudo extraer texto del PDF %s", path)
    return ""


def extract_pdf_info(pdf_path: str | Path) -> dict[str, Any]:
    """Metadatos básicos del PDF más el texto extraído (best-effort).

    ``num_pages`` se rellena siempre que sea posible (usa pypdf); si la lectura
    falla, devuelve ``None`` sin tumbar el flujo.

    Returns
    -------
    dict
        Claves: filename, path, num_pages, text.
    """
    path = Path(pdf_path)
    info: dict[str, Any] = {
        "filename": path.name,
        "path": str(path),
        "num_pages": None,
        "text": "",
    }

    # Páginas (best-effort — no debe impedir el procesamiento).
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        info["num_pages"] = len(reader.pages)
    except Exception as exc:  # pragma: no cover - dependencia externa
        logger.warning("No se pudo leer el número de páginas de %s: %s", path, exc)

    info["text"] = extract_text(path)
    return info


__all__ = ["extract_text", "extract_pdf_info"]

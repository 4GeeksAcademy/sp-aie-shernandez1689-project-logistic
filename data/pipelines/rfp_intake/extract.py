"""Conversión PDF → Markdown de una RFP (paso obligatorio del intake).

Toda RFP se convierte a Markdown con **MarkItDown** (``markitdown[pdf]``,
que usa pdfminer.six) **antes** de que el clasificador o cualquier agente la
lea: el resto del pipeline recibe el Markdown, nunca el PDF.

No hay fallback silencioso a otro extractor: si MarkItDown (o su extra
``[pdf]``) no está instalado se lanza ``ConversionUnavailableError`` para
que el fallo de entorno sea visible. Un PDF sin capa de texto (escaneado)
convierte a Markdown vacío y el orquestador lo descarta.

``pypdf`` se usa únicamente para contar páginas.

Funciones públicas
------------------
convert_pdf_to_markdown(pdf_path) -> dict
    ``filename``, ``num_pages``, ``markdown``, ``converter``.
save_markdown(markdown, target) -> Path
    Escribe el ``.md`` junto al PDF en ``data/raw/rfp/``.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_markitdown: Any | None = None


class ConversionUnavailableError(RuntimeError):
    """MarkItDown o su extra ``[pdf]`` no está instalado en el entorno."""


def _converter_name() -> str:
    try:
        return f"markitdown {version('markitdown')}"
    except PackageNotFoundError:  # pragma: no cover - entorno roto
        return "markitdown"


def _get_converter() -> Any:
    """Singleton perezoso de ``MarkItDown``."""
    global _markitdown
    if _markitdown is None:
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise ConversionUnavailableError(
                "markitdown no está instalado (uv add 'markitdown[pdf]')"
            ) from exc
        _markitdown = MarkItDown()
    return _markitdown


def _count_pages(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception as exc:  # best-effort: no debe impedir el procesamiento
        logger.warning("No se pudo leer el número de páginas de %s: %s", path, exc)
        return None


def convert_pdf_to_markdown(pdf_path: str | Path) -> dict[str, Any]:
    """Convierte un PDF a Markdown con MarkItDown.

    Returns
    -------
    dict
        ``filename``, ``path``, ``num_pages``, ``markdown`` (``''`` si el PDF
        no tiene texto) y ``converter`` (nombre + versión).

    Raises
    ------
    FileNotFoundError
        Si el PDF no existe.
    ConversionUnavailableError
        Si falta MarkItDown o su extra ``[pdf]``.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    converter = _get_converter()
    from markitdown import MissingDependencyException

    try:
        result = converter.convert(str(path))
    except MissingDependencyException as exc:
        raise ConversionUnavailableError(
            "markitdown sin soporte PDF (uv add 'markitdown[pdf]')"
        ) from exc
    except Exception as exc:
        # FileConversionException envuelve MissingDependencyException.
        if "MissingDependencyException" in str(exc):
            raise ConversionUnavailableError(
                "markitdown sin soporte PDF (uv add 'markitdown[pdf]')"
            ) from exc
        logger.warning("MarkItDown no pudo convertir %s: %s", path, exc)
        markdown = ""
    else:
        markdown = (result.text_content or "").strip()

    return {
        "filename": path.name,
        "path": str(path),
        "num_pages": _count_pages(path),
        "markdown": markdown,
        "converter": _converter_name(),
    }


def save_markdown(markdown: str, target: Path) -> Path:
    """Escribe el Markdown convertido (artefacto runtime junto al PDF)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown + "\n", encoding="utf-8")
    return target


__all__ = ["ConversionUnavailableError", "convert_pdf_to_markdown", "save_markdown"]

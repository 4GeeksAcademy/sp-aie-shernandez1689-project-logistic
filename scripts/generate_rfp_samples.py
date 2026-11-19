#!/usr/bin/env python3
"""Genera los 3 PDFs de prueba del tramo de intake de RFPs (BRIEFING §4).

Escribe en ``rfp-requests/trackflow/``:
  1. ``trackflow-request-1.pdf`` — RFP **formal** de ModaViva (España):
     almacenamiento + devoluciones, transportista propio para última milla.
     Activa ``warehouse`` y ``reverse`` (no ``lastmile``). Moneda EUR.
  2. ``trackflow-request-2.pdf`` — RFP **informal** (correo) de Luna
     Cosmetics (Los Ángeles): almacenamiento + última milla EE. UU.,
     ~5.000 pedidos/mes. Activa ``warehouse`` y ``lastmile``. Moneda USD.
  3. ``trackflow-request-3.pdf`` — pitch entrante de un transportista que
     vende sus tarifas a TrackFlow (NO es una RFP de cliente). El
     clasificador debe rechazarlo (``descartado``).

Uso:  python scripts/generate_rfp_samples.py
      python scripts/generate_rfp_samples.py --out rfp-requests/trackflow
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "rfp-requests" / "trackflow"

FILES = {
    "trackflow-request-1.pdf": "MODAVIVA_FORMAL_ES",
    "trackflow-request-2.pdf": "LUNA_INFORMAL_US",
    "trackflow-request-3.pdf": "CARRIER_PITCH_INVALID",
}


# ── Contenido de los 3 documentos (alineado con el clasificador) ────────────


def _formal_es_pdf() -> list:
    """trackflow-request-1: Solictud formal de ModaViva (ES, EUR)."""
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleX", parent=styles["Title"], fontSize=18, leading=22, spaceAfter=12
    )
    h2 = ParagraphStyle(
        "H2X", parent=styles["Heading2"], fontSize=13, leading=17, spaceBefore=10, spaceAfter=6
    )
    body = ParagraphStyle(
        "BodyX", parent=styles["BodyText"], fontSize=10, leading=14
    )
    return [
        Paragraph(
            "SOLICITUD DE PROPUESTA (RFP) — Servicios de Logística", title
        ),
        Paragraph("N.º RFP-2026-0173 &nbsp;|&nbsp; Fecha de emisión: 2 de marzo de 2026", body),
        Spacer(1, 6 * mm),
        Paragraph("1. Datos del solicitante", h2),
        Paragraph(
            "Client: ModaViva Fashion S.L.<br/>"
            "Empresa: ModaViva Fashion S.L.<br/>"
            "País: España — Zaragoza<br/>"
            "Contacto: Marta Ibáñez (Directora de Operaciones)",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph("2. Objeto de la solicitud", h2),
        Paragraph(
            "Somos una marca de moda y estamos buscando tercerizar la logística "
            "de nuestra operación en España. Solicitamos una propuesta para los "
            "siguientes servicios:",
            body,
        ),
        Spacer(1, 3 * mm),
        Table(
            [["Servicio", "Detalle"], ["Almacenamiento", "Centro logístico en Zaragoza"],
             ["Devoluciones", "Gestión de devoluciones y reacondicionamiento"]],
            colWidths=[55 * mm, 100 * mm],
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "Nota: para la última milla usamos nuestro propio transportista en "
            "España, por lo que la última milla NO está incluida en esta "
            "solicitud; únicamente solicitamos almacenamiento y devoluciones.",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph("3. Volumen y plazos", h2),
        Paragraph(
            "Volumen estimado: 3.000 pedidos al mes.<br/>"
            "Fecha límite para la propuesta: 2026-06-30.",
            body,
        ),
        Spacer(1, 4 * mm),
        Paragraph("4. Presupuesto de referencia", h2),
        Paragraph(
            "Presupuesto orientativo: EUR 50.000-80.000 anuales.",
            body,
        ),
    ]


def _informal_us_pdf() -> list:
    """trackflow-request-2: correo informal de Luna Cosmetics (US, USD)."""
    styles = getSampleStyleSheet()
    h2 = ParagraphStyle(
        "H2X", parent=styles["Heading2"], fontSize=13, leading=17, spaceBefore=10, spaceAfter=6
    )
    body = ParagraphStyle(
        "BodyX", parent=styles["BodyText"], fontSize=10, leading=14
    )
    return [
        Paragraph("Asunto: Necesitamos ayuda con nuestro almacenamiento y entregas", h2),
        Spacer(1, 3 * mm),
        Paragraph(
            "Client: Luna Cosmetics",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Hola equipo,",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "We are a cosmetics company in Los Angeles and we would like to "
            "outsource our US logistics. Necesitamos warehousing y last mile "
            "delivery para el mercado de EE. UU. (Los Angeles, LA).",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Nuestro volumen es de aproximadamente 5.000 orders per month. "
            "Fecha límite: 2026-05-15. Presupuesto: USD 40.000.",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Quedamos atentos.",
            body,
        ),
    ]


def _carrier_pitch_pdf() -> list:
    """trackflow-request-3: pitch entrante de tarifas de un transportista."""
    styles = getSampleStyleSheet()
    h2 = ParagraphStyle(
        "H2X", parent=styles["Heading2"], fontSize=13, leading=17, spaceBefore=10, spaceAfter=6
    )
    body = ParagraphStyle(
        "BodyX", parent=styles["BodyText"], fontSize=10, leading=14
    )
    return [
        Paragraph("Oferta comercial — Transportes Rápidos del Sur", h2),
        Spacer(1, 3 * mm),
        Paragraph(
            "Estimado TrackFlow:",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "As your logistics partner, we would like to offer you our "
            "partner carrier rates for your deliveries in Spain.",
            body,
        ),
        Spacer(1, 2 * mm),
        Paragraph(
            "Our price card is the following:",
            body,
        ),
        Spacer(1, 2 * mm),
        Table(
            [["Ruta", "Tarifa"], ["Zaragoza - Madrid", "4,80 EUR/paquete"],
             ["Zaragoza - Barcelona", "5,20 EUR/paquete"]],
            colWidths=[70 * mm, 85 * mm],
        ),
        Spacer(1, 3 * mm),
        Paragraph(
            "These are our rates for shipments. Contacto: ventas@transportesrapidos.example.",
            body,
        ),
    ]


# ── Render a PDF con reportlab ──────────────────────────────────────────────


def build_pdf(path: Path, story: list) -> None:
    """Escribe un PDF A4 con el contenido dado."""
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=path.stem,
    )
    doc.build(story)


def generate(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    builders = {
        "trackflow-request-1.pdf": _formal_es_pdf,
        "trackflow-request-2.pdf": _informal_us_pdf,
        "trackflow-request-3.pdf": _carrier_pitch_pdf,
    }
    written: list[Path] = []
    for filename, builder in builders.items():
        target = out_dir / filename
        build_pdf(target, builder())
        written.append(target)
        print(f"  ✔ {target.relative_to(REPO_ROOT)}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Directorio destino de los PDFs (por defecto rfp-requests/trackflow).",
    )
    args = parser.parse_args(argv)
    print("Generando PDFs de ejemplo del BRIEFING §4 …")
    generate(Path(args.out))
    print("Hecho.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
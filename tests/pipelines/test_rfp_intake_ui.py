"""E2E de la UI de recepción de RFPs (``uis/backoffice/rfp``) con Chromium.

Sube los PDF de muestra del BRIEFING §4 **por la interfaz** (input de archivo
+ botón) y comprueba lo que ve el usuario. Las RFP aceptadas pasan por la
Parte 1 (análisis) y la Parte 2 encadenada (borradores + evaluación):

- ``trackflow-request-1.pdf`` (RFP formal)   → ``esperando_aprobación``
- ``trackflow-request-2.pdf`` (RFP informal) → ``esperando_aprobación``
- ``trackflow-request-3.pdf`` (pitch de transportista) → ``descartado``

Entorno del test: la API real (``services/api/main.py``) en uvicorn con auth
JWT real (usuario TinyDB temporal + token firmado), pipeline real con
BackgroundTasks y SQLite en archivo en lugar de Supabase (mismos modelos
SQLModel). La UI se sirve como estático en ``http://127.0.0.1:5500`` (origen
de Live Server, permitido por defecto en ``CORS_ALLOW_ORIGINS``), distinto
del de la API, así que también valida CORS.

Con ``RFP_UI_SCREENSHOT=<ruta.png>`` guarda una captura de la página final.

Requiere Playwright + Chromium (``uv sync --group dev`` y
``playwright install --with-deps chromium``); si no están, se omite.
"""

from __future__ import annotations

import functools
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "services" / "api"
for _path in (REPO_ROOT, API_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"
SAMPLE_PDFS = [SAMPLES_DIR / f"trackflow-request-{i}.pdf" for i in (1, 2, 3)]
EXPECTED = {
    "trackflow-request-1.pdf": "esperando_aprobación",
    "trackflow-request-2.pdf": "esperando_aprobación",
    "trackflow-request-3.pdf": "descartado",
}
UI_TIMEOUT_MS = 30_000
UI_PORT = 5500  # origen permitido por defecto en CORS_ALLOW_ORIGINS

pytestmark = pytest.mark.skipif(
    not SAMPLE_PDFS[0].exists(), reason="Ejecuta primero scripts/generate_rfp_samples.py"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silencia el log del estático
        pass


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Exception as exc:  # Chromium no instalado / sin libs del sistema
            pytest.skip(f"Chromium no disponible: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """API real (uvicorn) + UI estática + usuario y token JWT reales."""
    import jwt
    import uvicorn
    from sqlmodel import create_engine
    from tinydb import TinyDB

    import data.pipelines.rfp_intake.db as db_mod
    import data.pipelines.rfp_intake.pipeline as pipeline_mod

    # Persistencia: SQLite en archivo con los modelos del esquema rfp_intake.
    # Una conexión por sesión (como el pool de PostgreSQL): la UI sube varios
    # PDF a la vez y sus análisis escriben en paralelo desde el threadpool;
    # una conexión compartida (StaticPool en memoria) no es segura entre hilos.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rfp_intake.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    ).execution_options(schema_translate_map={"rfp_intake": None})
    db_mod.create_tables(engine)
    db_mod.set_engine(engine)
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: raw_dir)

    # Auth real: usuario activo en TinyDB + token HS256 firmado.
    import auth

    users_db = tmp_path / "users.json"
    with TinyDB(users_db) as tiny:
        tiny.table("users").insert(
            {"id": "u-sales", "email": "ventas@trackflow.example", "role": "admin", "is_active": True}
        )
    monkeypatch.setattr(auth, "USER_DB_PATH", users_db)
    monkeypatch.setenv("JWT_SECRET_KEY", "e2e-secret")
    token = jwt.encode(
        {"sub": "u-sales", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "e2e-secret",
        algorithm=auth.JWT_ALGORITHM,
    )

    from main import app

    api_port, ui_port = _free_port(), UI_PORT
    try:
        static = ThreadingHTTPServer(
            ("127.0.0.1", ui_port),
            functools.partial(_QuietHandler, directory=str(REPO_ROOT / "uis")),
        )
    except OSError:
        pytest.skip(f"Puerto {ui_port} ocupado (origen CORS por defecto de la UI)")
    threading.Thread(target=static.serve_forever, daemon=True).start()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
    )
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()


    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn no arrancó"

    yield {
        "api": f"http://127.0.0.1:{api_port}",
        "ui": f"http://127.0.0.1:{ui_port}/backoffice/rfp/index.html",
        "token": token,
        "raw_dir": raw_dir,
        "db": db_mod,
    }

    server.should_exit = True
    api_thread.join(timeout=5)
    static.shutdown()
    db_mod.set_engine(None)


@pytest.fixture
def page(browser, stack):
    context = browser.new_context()
    context.add_init_script(
        "localStorage.setItem('trackflow_access_token', %r);"
        "localStorage.setItem('trackflow_api_base', %r);" % (stack["token"], stack["api"])
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.errors = errors  # type: ignore[attr-defined]
    yield page
    context.close()


def test_upload_brief_samples_through_ui(page, stack):
    """Formal e informal se aceptan; el pitch de transportista se rechaza."""
    page.goto(stack["ui"])
    sync_api.expect(page.locator("#ticketsBody")).to_contain_text("Sin tickets")

    page.set_input_files("#fileInput", [str(p) for p in SAMPLE_PDFS])
    page.click("#uploadBtn")

    # Cada PDF crea su ticket y la fila de subida acaba en su estado final.
    rows = page.locator("#uploadList .upload-row")
    sync_api.expect(rows).to_have_count(3, timeout=UI_TIMEOUT_MS)
    for filename, status in EXPECTED.items():
        row = rows.filter(has_text=filename)
        sync_api.expect(row.locator(f".status-{status}")).to_be_visible(timeout=UI_TIMEOUT_MS)
    sync_api.expect(page.locator("#uploadFeedback")).to_have_text(
        "Análisis terminado.", timeout=UI_TIMEOUT_MS
    )

    # La tabla de tickets refleja los 3 tickets con su estado.
    table = page.locator("#ticketsBody tr")
    sync_api.expect(table).to_have_count(3, timeout=UI_TIMEOUT_MS)
    statuses = sorted(table.locator(".status-pill").all_inner_texts())
    assert [s.lower() for s in statuses] == ["descartado", "esperando_aprobación", "esperando_aprobación"]
    sync_api.expect(page.locator("#ticketsBody")).to_contain_text("ModaViva Fashion S.L.")
    sync_api.expect(page.locator("#ticketsBody")).to_contain_text("Luna Cosmetics")

    # Los PDF quedaron bajo data/raw (dir runtime del test) junto a su .md.
    assert len(list(stack["raw_dir"].glob("*.pdf"))) == 3
    assert len(list(stack["raw_dir"].glob("*.md"))) == 3

    # Detalle de la RFP formal: resumen para Ventas + departamentos correctos.
    page.locator("#ticketsBody tr", has_text="ModaViva").locator("a.client-row").click()
    detail = page.locator("#detailContent")
    sync_api.expect(detail).to_contain_text("Resumen para Ventas", timeout=UI_TIMEOUT_MS)
    sync_api.expect(detail).to_contain_text("Ana Whitfield")
    sync_api.expect(detail).to_contain_text("Sofía Ramos")
    assert "Carlos Vega" not in detail.inner_text()  # usa su propio transportista
    sync_api.expect(detail).to_contain_text("generando_borrador")

    # Parte 2: un borrador evaluado por departamento activo.
    sync_api.expect(detail).to_contain_text("Propuesta económica (Parte 2)")
    proposal = detail.locator(".detail-section", has_text="Propuesta económica (Parte 2)")
    sync_api.expect(proposal.locator(".status-evaluacion_superada")).to_have_count(2)
    sync_api.expect(proposal).to_contain_text("aprobada por los evaluadores")
    proposal.locator("summary", has_text="Ver borrador").first.click()
    sync_api.expect(proposal).to_contain_text("Descuentos por volumen")

    if os.getenv("RFP_UI_SCREENSHOT"):
        page.screenshot(path=os.environ["RFP_UI_SCREENSHOT"], full_page=True)
    assert page.errors == []  # type: ignore[attr-defined]


def test_ui_rejects_non_pdf(page, stack, tmp_path):
    """Un archivo con extensión .pdf pero sin cabecera PDF no crea ticket."""
    fake = tmp_path / "nota.pdf"
    fake.write_text("esto no es un PDF")
    page.goto(stack["ui"])
    page.set_input_files("#fileInput", str(fake))
    page.click("#uploadBtn")
    sync_api.expect(page.locator("#uploadList .upload-note")).to_contain_text(
        "no es un PDF válido", timeout=UI_TIMEOUT_MS
    )
    assert stack["db"].list_tickets() == []


def test_department_approval_through_ui(page, stack):
    """Parte 3 por la interfaz: cada responsable decide su sección hasta el documento final."""
    page.goto(stack["ui"])
    page.set_input_files("#fileInput", str(SAMPLE_PDFS[0]))  # ModaViva: warehouse + reverse
    page.click("#uploadBtn")
    row = page.locator("#uploadList .upload-row")
    sync_api.expect(row.locator(".status-esperando_aprobación")).to_be_visible(timeout=UI_TIMEOUT_MS)

    page.locator("#ticketsBody tr", has_text="ModaViva").locator("a.client-row").click()
    detail = page.locator("#detailContent")
    approval = detail.locator(".approval-section")
    sync_api.expect(approval).to_contain_text("Aprobación por departamento (Parte 3)", timeout=UI_TIMEOUT_MS)
    assert page.locator("#regenerateBtn").count() == 0  # con la Parte 3 abierta no se regenera

    # Sofía pide cambios sin comentario: la UI no deja enviar.
    reverse = approval.locator('.approval-card[data-department="reverse"]')
    reverse.get_by_label("Solicitar cambios").check()
    reverse.get_by_role("button", name="Registrar decisión").click()
    sync_api.expect(reverse.locator(".feedback")).to_contain_text("Explica el motivo")

    # Con comentario: la sección se reescribe y vuelve a esperar a Sofía (ronda 1).
    reverse.locator("textarea").fill("Detallar el proceso de inspección de las devoluciones.")
    reverse.get_by_role("button", name="Registrar decisión").click()
    reverse = page.locator('.approval-card[data-department="reverse"]')
    sync_api.expect(reverse).to_contain_text("Rondas de cambios: 1/3", timeout=UI_TIMEOUT_MS)
    sync_api.expect(reverse.locator(".status-approval-cambios_solicitados")).to_be_visible()

    # B (reverse) se aprueba mientras A (warehouse) sigue esperando a Ana.
    reverse.get_by_label("Aprobar").check()
    reverse.get_by_role("button", name="Registrar decisión").click()
    reverse = page.locator('.approval-card[data-department="reverse"]')
    sync_api.expect(reverse.locator(".status-approval-aprobada")).to_be_visible(timeout=UI_TIMEOUT_MS)
    warehouse = page.locator('.approval-card[data-department="warehouse"]')
    sync_api.expect(warehouse.locator(".decision-form")).to_be_visible()
    sync_api.expect(page.locator("#detailContent")).to_contain_text("esperando_aprobación")

    warehouse.get_by_label("Aprobar").check()
    warehouse.get_by_role("button", name="Registrar decisión").click()
    final = page.locator("#detailContent .final-document")
    sync_api.expect(final).to_contain_text("Documento final", timeout=UI_TIMEOUT_MS)
    sync_api.expect(final).to_contain_text("# Propuesta económica TrackFlow — ModaViva")
    sync_api.expect(page.locator("#detailContent")).to_contain_text("terminado")
    sync_api.expect(page.locator("#ticketsBody .status-terminado")).to_be_visible(timeout=UI_TIMEOUT_MS)
    assert page.locator(".decision-form").count() == 0

    if os.getenv("RFP_UI_SCREENSHOT"):
        page.screenshot(path=os.environ["RFP_UI_SCREENSHOT"].replace(".png", "-part3.png"), full_page=True)
    assert page.errors == []  # type: ignore[attr-defined]

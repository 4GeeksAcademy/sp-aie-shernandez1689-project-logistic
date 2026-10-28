"""Tests del tramo de intake de RFPs (Hito 9, Parte 1).

Cubren, de abajo hacia arriba:

- **Clasificador** (unitarios): regresiones críticas del parser determinista —
  país (la "la" española no debe ser US/LA), nombre de cliente (no capturar
  "Servicios de Logística"), volumen ("3.000 pedidos al mes" → 3000).
- **Orquestador** ``process_rfp_pdf``: flujo completo sobre los PDFs de
  muestra del BRIEFING §4 (sin persistir) usando ``persist=False``.
- **Router HTTP** ``/rfp/*`` de ``services/api``: upload multipart,
  listado y detalle con la dependencia de auth sobreescrita.

- **Persistencia** ``db.py``: roundtrip SQLModel de Ticket, metadatos y
  ``DepartmentSection.key_aspects`` contra SQLite en memoria (mismos modelos
  que en PostgreSQL; el esquema ``rfp_intake`` se traduce con
  ``schema_translate_map``). Sin ``DATABASE_URL`` persistir falla — no hay
  fallback a archivos.

El orquestador se prueba con ``persist=False`` y el router con
``TestClient`` + ``app.dependency_overrides``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── Acceso al monorepo ─────────────────────────────────────────────────────
# Este archivo vive en <repo>/tests/pipelines/ → parents[2] = <repo>.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import data.pipelines.rfp_intake.classifier as classifier  # noqa: E402
from data.pipelines.rfp_intake.classifier import classify  # noqa: E402
from data.pipelines.rfp_intake.pipeline import (  # noqa: E402
    process_rfp_pdf,
    resolve_currency,
)

SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"
SAMPLE_PDFS = {i: SAMPLES_DIR / f"trackflow-request-{i}.pdf" for i in (1, 2, 3)}

needs_samples = pytest.mark.skipif(
    not SAMPLE_PDFS[1].exists(),
    reason="Ejecuta primero scripts/generate_rfp_samples.py",
)


# ===================================================================
# Unitarios del clasificador — país (regresión "la" española)
# ===================================================================


class TestCountryDetection:
    def test_spanish_la_not_us(self):
        """'la' española NO debe disparar el patrón US (bug corregido)."""
        text = "para la última milla de la propuesta y de la logística"
        assert classifier._detect_country(text) is None

    def test_spanish_espana(self):
        assert classifier._detect_country("Empresa en España") == "Spain"

    def test_us_los_angeles(self):
        assert classifier._detect_country("based in Los Angeles, CA") == "US"

    def test_us_abbreviation(self):
        assert classifier._detect_country("operamos en US") == "US"

    def test_us_eeuu_abbreviation(self):
        assert classifier._detect_country("cobertura en EE. UU.") == "US"


# ===================================================================
# Unitarios del clasificador — nombre de cliente (regresión "de Logística")
# ===================================================================


class TestClientNameDetection:
    def test_explicit_client_label(self):
        """Etiqueta explícita 'Client:' → nombre de marca completo."""
        text = "Client: ModaViva Fashion S.L.\nWe need warehouse services."
        assert classifier._detect_client_name(text) == "ModaViva Fashion S.L."

    def test_de_logistica_not_captured(self):
        """'Servicios de Logística' NO debe capturar 'Log' (bug corregido)."""
        text = (
            "SOLICITUD DE PROPUESTA (RFP) — Servicios de Logística\n"
            "Client: ModaViva Fashion S.L."
        )
        assert classifier._detect_client_name(text) == "ModaViva Fashion S.L."

    def test_somos_pattern(self):
        """'Somos <Nombre>' como señal de cliente."""
        text = "Somos ModaViva Fashion S.L. y queremos tercerizar"
        assert classifier._detect_client_name(text) == "ModaViva Fashion S.L."

    def test_empty_returns_none(self):
        assert classifier._detect_client_name("") is None

    def test_stopword_rejected(self):
        """Palabras tipo 'PROPUESTA' no se devuelven como marca."""
        text = "SOLICITUD DE PROPUESTA (RFP)"
        assert classifier._detect_client_name(text) is None


# ===================================================================
# Unitarios del clasificador — volumen (regresión "3.000 pedidos al mes")
# ===================================================================


class TestVolumeDetection:
    def test_pedidos_al_mes(self):
        """'3.000 pedidos al mes' → 3000 (bug de precedencia corregido)."""
        assert classifier._detect_volume("3.000 pedidos al mes") == 3000

    def test_orders_per_month(self):
        assert classifier._detect_volume("5,000 orders per month") == 5000

    def test_al_mes_leading_number(self):
        """'al mes 2.500' (número tras la señal)."""
        assert classifier._detect_volume("volumen al mes: 2.500") == 2500

    def test_slash_format(self):
        assert classifier._detect_volume("1.200 pedidos/mes") == 1200

    def test_no_volume(self):
        assert classifier._detect_volume("sin volumen definido") is None


# ===================================================================
# Unitarios del clasificador — presupuesto, deadline, currency, routing
# ===================================================================


class TestBudgetAndDeadline:
    def test_budget_eur_range(self):
        assert classifier._detect_budget("EUR 50.000-80.000") == "EUR 50.000-80.000"

    def test_budget_usd(self):
        assert classifier._detect_budget("Budget: USD 40.000") == "USD 40.000"

    def test_deadline_iso(self):
        assert classifier._detect_deadline("Fecha límite: 2026-06-30") == "2026-06-30"

    def test_unknown_budget(self):
        assert classifier._detect_budget("sin presupuesto") is None


class TestCurrencyResolution:
    def test_spain_eur(self):
        assert resolve_currency("Spain") == "EUR"

    def test_us_usd(self):
        assert resolve_currency("US") == "USD"

    def test_unknown(self):
        """País desconocido → sin moneda (se pide confirmar)."""
        assert resolve_currency(None) is None
        assert resolve_currency("") is None


# ===================================================================
# Clasificación integrada — los 3 escenarios del BRIEFING §4
# ===================================================================


class TestClassifyScenarios:
    def test_modaviva_formal_es(self):
        """Escenario 1: RFP formal de ModaViva (ES, EUR, sin última milla)."""
        text = (
            "SOLICITUD DE PROPUESTA (RFP) — Servicios de Logística\n"
            "Client: ModaViva Fashion S.L.\n"
            "Empresa en España. Somos una marca de moda y estamos buscando "
            "tercerizar los servicios de almacenamiento y gestión de "
            "devoluciones. Usamos nuestro propio transportista, pero "
            "necesitamos que un operador gestione el almacén y las "
            "devoluciones.\n"
            "Actualmente gestionamos unos 3.000 pedidos al mes.\n"
            "Fecha límite: 2026-06-30\n"
            "Presupuesto estimado: EUR 50.000-80.000"
        )
        result = classify(text)
        assert result.is_rfp is True
        assert result.doc_type == "formal"
        assert result.client_name == "ModaViva Fashion S.L."
        assert result.client_country == "Spain"
        assert result.monthly_volume == 3000
        assert result.budget_range == "EUR 50.000-80.000"
        assert result.deadline == "2026-06-30"
        # Última milla NO se activa porque usan transportista propio.
        assert result.departments_needed == ["warehouse", "reverse"]

    def test_luna_informal_us(self):
        """Escenario 2: RFP informal de Luna Cosmetics (US, USD, última milla)."""
        text = (
            "Subject: logistics services\n"
            "Hi team, we are based in Los Angeles and handle about 5.000 "
            "orders per month. We need warehouse storage and last-mile "
            "delivery across US.\n"
            "Client: Luna Cosmetics\n"
            "Budget: USD 40.000\n"
            "Deadline: 2026-05-15"
        )
        result = classify(text)
        assert result.is_rfp is True
        assert result.doc_type == "informal"
        assert result.client_name == "Luna Cosmetics"
        assert result.client_country == "US"
        assert result.monthly_volume == 5000
        assert result.budget_range == "USD 40.000"
        assert result.deadline == "2026-05-15"
        assert result.departments_needed == ["warehouse", "lastmile"]

    def test_carrier_pitch_rejected(self):
        """Escenario 3: pitch de vendor NO es una RFP de cliente."""
        text = (
            "Dear TrackFlow team, as your logistics partner we would like to "
            "offer you our partner carrier rates. Our price card is attached "
            "with our tariffs."
        )
        result = classify(text)
        assert result.is_rfp is False
        assert result.doc_type == "invalid"

    def test_empty_rejected(self):
        result = classify("")
        assert result.is_rfp is False


# ===================================================================
# Orquestador — flujo completo sobre los PDFs de muestra (sin persistir)
# ===================================================================


class TestPipelineOrchestrator:
    @needs_samples
    @pytest.mark.parametrize("idx,expected", [
        (1, {"status": "analisis_completo", "doc_type": "formal",
             "client": "ModaViva Fashion S.L.", "country": "Spain",
             "currency": "EUR", "volume": 3000,
             "depts": ["warehouse", "reverse"]}),
        (2, {"status": "analisis_completo", "doc_type": "informal",
             "client": "Luna Cosmetics", "country": "US",
             "currency": "USD", "volume": 5000,
             "depts": ["warehouse", "lastmile"]}),
        (3, {"status": "descartado"}),
    ])
    def test_process_sample(self, tmp_path, idx, expected):
        """``process_rfp_pdf`` sobre la muestra del BRIEFING §4."""
        pdf = SAMPLE_PDFS[idx]
        result = process_rfp_pdf(pdf, raw_dir=tmp_path, persist=False)

        assert result["status"] == expected["status"]
        if expected["status"] == "descartado":
            assert result.get("reason")
            return

        classification = result["classification"]
        metadata = result["metadata"]
        assert classification["doc_type"] == expected["doc_type"]
        assert classification["client_name"] == expected["client"]
        assert classification["client_country"] == expected["country"]
        assert metadata["monthly_volume"] == expected["volume"]
        assert result["currency"] == expected["currency"]
        depts = [d["department_id"] for d in result["departments"]]
        assert depts == expected["depts"]
        # Composición esperada de la respuesta.
        assert result["ticket_id"]
        assert result["source_pdf"]["filename"] == pdf.name


# ===================================================================
# Persistencia — SQLModel (PostgreSQL en producción, SQLite en tests)
# ===================================================================


@pytest.fixture
def sqlite_db(monkeypatch):
    """Engine SQLite en memoria con las tablas del esquema ``rfp_intake``."""
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import data.pipelines.rfp_intake.db as db_mod

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"rfp_intake": None})
    db_mod.create_tables(engine)
    db_mod.set_engine(engine)
    yield db_mod
    db_mod.set_engine(None)


class TestPersistence:
    def test_save_intake_roundtrip(self, sqlite_db):
        """Ticket + metadatos + key_aspects se guardan y se leen por SQLModel."""
        ticket = {"ticket_id": "rfp_t1", "rfp_id": "rfp_t1-rfp",
                  "status": "analisis_completo", "raw_pdf_path": "/tmp/x.pdf",
                  "reason": "ok", "created_at": "2026-10-28T10:00:00+00:00",
                  "updated_at": "2026-10-28T10:00:00+00:00"}
        metadata = {"id": "rfp_t1-meta", "ticket_id": "rfp_t1",
                    "rfp_id": "rfp_t1-rfp", "doc_type": "formal",
                    "client_country": "Spain", "currency": "EUR",
                    "services_requested": ["warehouse", "reverse"],
                    "departments_needed": ["warehouse", "reverse"],
                    "monthly_volume": 3000, "readability": {"words": 10}}
        sections = [
            {"id": f"rfp_t1_{d}", "ticket_id": "rfp_t1", "rfp_id": "rfp_t1-rfp",
             "department_id": d, "key_aspects": [f"Servicio solicitado: {d}"]}
            for d in ("warehouse", "reverse")
        ]
        sqlite_db.save_intake(ticket, metadata, sections)

        stored = sqlite_db.get_ticket("rfp_t1")
        assert stored["status"] == "analisis_completo"
        assert stored["created_at"].startswith("2026-10-28T10:00:00")
        meta = sqlite_db.get_rfp_metadata("rfp_t1")
        assert meta["currency"] == "EUR"
        assert meta["doc_type"] == "formal"
        assert meta["readability"] == {"words": 10}
        secs = sqlite_db.get_department_sections("rfp_t1")
        assert [s["department_id"] for s in secs] == ["reverse", "warehouse"]
        assert secs[1]["key_aspects"] == ["Servicio solicitado: warehouse"]
        assert secs[1]["approval_status"] == "pendiente"

    def test_save_ticket_upserts(self, sqlite_db):
        """Guardar el mismo ticket dos veces actualiza, no duplica."""
        base = {"ticket_id": "rfp_t2", "rfp_id": "r", "status": "analizando",
                "raw_pdf_path": "/tmp/x.pdf"}
        sqlite_db.save_ticket(base)
        sqlite_db.save_ticket({**base, "status": "descartado"})
        tickets = sqlite_db.list_tickets()
        assert len(tickets) == 1
        assert tickets[0]["status"] == "descartado"

    @needs_samples
    def test_process_sample_persists(self, sqlite_db, tmp_path):
        """El orquestador deja ticket + secciones activas en la BD."""
        result = process_rfp_pdf(SAMPLE_PDFS[2], raw_dir=tmp_path)
        ticket_id = result["ticket_id"]
        assert sqlite_db.get_ticket(ticket_id)["status"] == "analisis_completo"
        assert sqlite_db.get_rfp_metadata(ticket_id)["currency"] == "USD"
        secs = sqlite_db.get_department_sections(ticket_id)
        assert [s["department_id"] for s in secs] == ["lastmile", "warehouse"]
        assert all(s["key_aspects"] for s in secs)

    def test_persist_without_database_fails(self, monkeypatch, tmp_path):
        """Sin DATABASE_URL no hay fallback a archivos: se rechaza persistir."""
        import data.pipelines.rfp_intake.db as db_mod

        monkeypatch.delenv("DATABASE_URL", raising=False)
        db_mod.set_engine(None)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with pytest.raises(db_mod.DatabaseNotConfiguredError):
            process_rfp_pdf(pdf, raw_dir=tmp_path)
        with pytest.raises(db_mod.DatabaseNotConfiguredError):
            db_mod.list_tickets()


# ===================================================================
# Router HTTP — /rfp/upload, /rfp/tickets, /rfp/tickets/{id}
# ===================================================================


class TestRfpRoutes:
    @pytest.fixture
    def api_env(self, monkeypatch):
        """App FastAPI + auth sobreescrita + persistencia desactivada."""
        API_DIR = REPO_ROOT / "services" / "api"
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))

        import data.pipelines.rfp_intake.db as db_mod
        from main import app

        # No escribir en datos reales durante los tests de rutas.
        monkeypatch.setattr(db_mod, "is_db_available", lambda: True)
        monkeypatch.setattr(db_mod, "save_intake", lambda *a, **k: None)
        monkeypatch.setattr(db_mod, "save_ticket", lambda *a, **k: None)
        monkeypatch.setattr(
            db_mod, "save_rfp_metadata", lambda *a, **k: None
        )
        monkeypatch.setattr(
            db_mod, "save_department_section", lambda *a, **k: None
        )
        # Simula almacenes separados: el ticket solo tiene id/status; los
        # campos de display viven en el registro de metadatos (es el contrato
        # real de db.py). El router las fusiona vía `_enrich_ticket`.
        monkeypatch.setattr(
            db_mod, "list_tickets",
            lambda: [{"ticket_id": "rfp_demo", "status": "analisis_completo",
                      "reason": "RFP demo", "raw_pdf_path": "/tmp/demo.pdf",
                      "created_at": "now", "updated_at": "now"}],
        )
        monkeypatch.setattr(
            db_mod, "get_rfp_metadata",
            lambda ticket_id: {
                "ticket_id": ticket_id,
                "doc_type": "formal",
                "client_name": "ModaViva Fashion S.L.",
                "client_country": "Spain",
                "services_requested": ["warehouse", "reverse"],
                "monthly_volume": 3000,
                "deadline": "2026-06-30",
                "budget_range": "EUR 50.000-80.000",
                "departments_needed": ["warehouse", "reverse"],
                "currency": "EUR",
            },
        )
        monkeypatch.setattr(
            db_mod, "get_ticket",
            lambda ticket_id: None if ticket_id == "inexistente" else {
                "ticket_id": ticket_id, "status": "analisis_completo",
                "reason": "RFP demo", "raw_pdf_path": "/tmp/demo.pdf",
                "created_at": "now", "updated_at": "now",
            },
        )
        monkeypatch.setattr(
            db_mod, "get_department_sections",
            lambda ticket_id: [
                {"id": "rfp_demo_warehouse", "ticket_id": ticket_id,
                 "department_id": "warehouse", "department_label": "Warehouse",
                 "department_owner": "Ana Whitfield",
                 "key_aspects": ["aspecto 1"]},
            ],
        )
        monkeypatch.setattr(
            db_mod, "list_tickets",
            lambda: [{"ticket_id": "rfp_demo", "status": "analisis_completo",
                      "client_name": "ModaViva Fashion S.L.",
                      "client_country": "Spain", "currency": "EUR",
                      "monthly_volume": 3000,
                      "departments_needed": ["warehouse", "reverse"]}],
        )

        from auth import CurrentUser, get_current_user
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )

        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()

    @needs_samples
    def test_upload_pdf_returns_ticket(self, api_env):
        """POST /rfp/upload con la muestra 1 → analisis_completo + EUR."""
        pdf = SAMPLE_PDFS[1]
        with pdf.open("rb") as handle:
            resp = api_env.post(
                "/rfp/upload",
                files={"file": (pdf.name, handle, "application/pdf")},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "analisis_completo"
        assert body["classification"]["client_name"] == "ModaViva Fashion S.L."
        assert body["classification"]["client_country"] == "Spain"
        assert body["currency"] == "EUR"
        assert body["metadata"]["monthly_volume"] == 3000

    @needs_samples
    def test_upload_vendor_pitch_rejected(self, api_env):
        """POST /rfp/upload con la muestra 3 → descartado."""
        pdf = SAMPLE_PDFS[3]
        with pdf.open("rb") as handle:
            resp = api_env.post(
                "/rfp/upload",
                files={"file": (pdf.name, handle, "application/pdf")},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "descartado"

    def test_upload_non_pdf_rejected(self, api_env, tmp_path):
        """POST /rfp/upload con .txt → 422."""
        bad = tmp_path / "nota.txt"
        bad.write_text("esto no es un PDF")
        with bad.open("rb") as handle:
            resp = api_env.post(
                "/rfp/upload",
                files={"file": ("nota.txt", handle, "text/plain")},
            )
        assert resp.status_code == 422

    def test_list_tickets_requires_auth_shape(self, api_env):
        """GET /rfp/tickets → {'tickets': [...], 'count': N}."""
        resp = api_env.get("/rfp/tickets")
        assert resp.status_code == 200
        body = resp.json()
        assert "tickets" in body and "count" in body
        assert body["count"] == len(body["tickets"])

    def test_list_tickets_enriched_with_metadata(self, api_env):
        """La fila del ticket fusiona metadatos: client, moneda, depts."""
        resp = api_env.get("/rfp/tickets")
        assert resp.status_code == 200
        ticket = resp.json()["tickets"][0]
        assert ticket["ticket_id"] == "rfp_demo"
        assert ticket["status"] == "analisis_completo"
        # Campos de display fundidos desde el registro de metadatos.
        assert ticket["client_name"] == "ModaViva Fashion S.L."
        assert ticket["client_country"] == "Spain"
        assert ticket["currency"] == "EUR"
        assert ticket["monthly_volume"] == 3000
        assert ticket["departments_needed"] == ["warehouse", "reverse"]
        assert ticket["doc_type"] == "formal"

    def test_get_ticket_detail_merges_metadata_and_sections(self, api_env):
        """Detalle: ticket fusionado + metadatos + secciones por departamento."""
        resp = api_env.get("/rfp/tickets/rfp_demo")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticket"]["doc_type"] == "formal"
        assert body["ticket"]["client_name"] == "ModaViva Fashion S.L."
        assert body["metadata"]["currency"] == "EUR"
        assert body["metadata"]["monthly_volume"] == 3000
        assert len(body["departments"]) == 1
        assert body["departments"][0]["department_id"] == "warehouse"
        assert "Ana Whitfield" in body["departments"][0]["department_owner"]

    @needs_samples
    def test_get_ticket_detail_missing_404(self, api_env):
        """GET /rfp/tickets/inexistente → 404 (sin aston erros)."""
        resp = api_env.get("/rfp/tickets/inexistente")
        assert resp.status_code == 404

    def test_list_tickets_without_database_503(self, api_env, monkeypatch):
        """Sin BD configurada el router responde 503 (no 500)."""
        import data.pipelines.rfp_intake.db as db_mod

        def _raise():
            raise db_mod.DatabaseNotConfiguredError("sin BD")

        monkeypatch.setattr(db_mod, "list_tickets", _raise)
        resp = api_env.get("/rfp/tickets")
        assert resp.status_code == 503

    def test_unauthenticated_401(self):
        """Sin token → 401 (auth real, no sobreescrita)."""
        API_DIR = REPO_ROOT / "services" / "api"
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        from fastapi.testclient import TestClient
        from main import app

        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                resp = client.get("/rfp/tickets")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 401
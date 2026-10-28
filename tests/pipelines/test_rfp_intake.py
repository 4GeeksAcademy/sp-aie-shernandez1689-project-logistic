"""Tests del tramo de intake de RFPs (Hito 9, Parte 1).

Cubren, de abajo hacia arriba:

- **Clasificador** (unitarios): regresiones críticas del parser determinista —
  país (la "la" española no debe ser US/LA), nombre de cliente (no capturar
  "Servicios de Logística"), volumen ("3.000 pedidos al mes" → 3000).
- **Orquestador** ``process_rfp_pdf``: flujo completo sobre los PDFs de
  muestra del BRIEFING §4 (sin persistir) usando ``persist=False``.
- **Modo ticket**: ``register_upload`` deja el ticket ``analizando`` y el PDF
  en ``data/raw/``; ``analyze_ticket`` (segundo plano) lo cierra.
- **Router HTTP** ``/rfp/*`` de ``services/api``: upload multipart (202 +
  análisis en ``BackgroundTasks``), listado y detalle.

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
    analyze_ticket,
    process_rfp_pdf,
    register_upload,
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
        assert result.services_requested == ["warehouse", "reverse"]

    def test_excluded_last_mile_not_a_requested_service(self):
        """'la última milla NO está incluida' no cuenta como servicio solicitado."""
        text = (
            "Somos una marca de moda y estamos buscando tercerizar la logística.\n"
            "Servicios: almacenamiento y devoluciones.\n"
            "Nota: para la última milla usamos nuestro propio transportista, por lo "
            "que la última milla NO está incluida en esta solicitud."
        )
        result = classify(text)
        assert result.services_requested == ["warehouse", "reverse"]
        assert "lastmile" not in result.departments_needed

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
# Conversión PDF → Markdown + legibilidad / costo de procesamiento
# ===================================================================


class TestMarkdownConversion:
    @needs_samples
    def test_markitdown_converts_sample(self):
        from data.pipelines.rfp_intake.extract import convert_pdf_to_markdown

        doc = convert_pdf_to_markdown(SAMPLE_PDFS[1])
        assert doc["converter"].startswith("markitdown")
        assert doc["num_pages"] == 1
        assert "ModaViva Fashion S.L." in doc["markdown"]

    def test_missing_markitdown_is_explicit(self, monkeypatch, tmp_path):
        """Sin MarkItDown no hay fallback silencioso: error explícito."""
        import builtins

        import data.pipelines.rfp_intake.extract as extract_mod

        real_import = builtins.__import__

        def _no_markitdown(name, *args, **kwargs):
            if name == "markitdown":
                raise ImportError("no markitdown")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(extract_mod, "_markitdown", None)
        monkeypatch.setattr(builtins, "__import__", _no_markitdown)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with pytest.raises(extract_mod.ConversionUnavailableError):
            extract_mod.convert_pdf_to_markdown(pdf)

    @needs_samples
    def test_pipeline_classifies_markdown_and_saves_md(self, tmp_path, monkeypatch):
        """El clasificador recibe el Markdown y el .md queda junto al PDF."""
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        seen = {}
        real_classify = pipeline_mod.classify

        def _spy(text):
            seen["text"] = text
            return real_classify(text)

        monkeypatch.setattr(pipeline_mod, "classify", _spy)
        result = process_rfp_pdf(SAMPLE_PDFS[1], raw_dir=tmp_path, persist=False)
        md_path = Path(result["metadata"]["markdown_path"])
        assert md_path == tmp_path / f"{result['ticket_id']}.md"
        assert md_path.read_text(encoding="utf-8").strip() == seen["text"]
        assert result["metadata"]["converter"].startswith("markitdown")


class TestReadabilityMetrics:
    def test_volume_metrics_always_present(self):
        from data.pipelines.rfp_intake.readability_metrics import (
            compute_readability_metrics,
        )

        stats = compute_readability_metrics("## Hola\n\n| a | b |\nTexto corto.")
        assert stats["num_words"] > 0
        assert stats["estimated_tokens"] > 0
        assert stats["flesch"] is None  # < 100 palabras
        assert stats["processing_cost"] == "bajo"
        assert "#" not in str(stats)  # sin sintaxis Markdown contada

    @needs_samples
    def test_scores_computed_for_long_text(self):
        """Regresión: los índices devolvían siempre None (objeto vs float)."""
        from data.pipelines.rfp_intake.extract import convert_pdf_to_markdown
        from data.pipelines.rfp_intake.readability_metrics import (
            compute_readability_metrics,
        )

        stats = compute_readability_metrics(
            convert_pdf_to_markdown(SAMPLE_PDFS[1])["markdown"]
        )
        assert stats["num_words"] >= 100
        assert isinstance(stats["flesch_kincaid"], float)
        assert isinstance(stats["ari"], float)

    @pytest.mark.parametrize("tokens,grade,wps,expected", [
        (239, 17.6, 19.0, "bajo"),   # corto: la densidad no sube el nivel
        (2000, 10.0, 12.0, "medio"),
        (2000, 15.0, 12.0, "alto"),  # medio + denso
        (2000, None, 30.0, "alto"),
        (7000, None, None, "alto"),
    ])
    def test_processing_cost_tiers(self, tokens, grade, wps, expected):
        from data.pipelines.rfp_intake.readability_metrics import processing_cost

        assert processing_cost(tokens, grade, wps) == expected


# ===================================================================
# Grafo orchestrator → workers → synthesizer
# ===================================================================


def _sample_state(idx):
    from data.pipelines.rfp_intake.extract import convert_pdf_to_markdown

    markdown = convert_pdf_to_markdown(SAMPLE_PDFS[idx])["markdown"]
    metadata = classify(markdown).to_dict()
    metadata["currency"] = resolve_currency(metadata["client_country"])
    return markdown, metadata


class TestDepartmentGraph:
    @needs_samples
    def test_orchestrator_one_task_per_active_department(self):
        from data.pipelines.rfp_intake.graph import fan_out, orchestrator

        markdown, metadata = _sample_state(1)
        state = {"ticket_id": "t", "rfp_id": "r", "markdown": markdown, "metadata": metadata}
        tasks = orchestrator(state)["tasks"]
        assert [t["department_id"] for t in tasks] == ["warehouse", "reverse"]
        sends = fan_out({**state, "tasks": tasks})
        assert [s.node for s in sends] == ["worker", "worker"]
        # Cada worker recibe solo extractos de su departamento.
        reverse = next(t for t in tasks if t["department_id"] == "reverse")
        assert any("devoluciones" in e.lower() for e in reverse["excerpts"])
        assert all(len(e.split()) >= 3 for t in tasks for e in t["excerpts"])
        assert tasks[0]["shared"]["monthly_volume"] == 3000

    @needs_samples
    @pytest.mark.parametrize("idx", [1, 2])
    def test_key_aspects_never_invent_numbers(self, idx):
        """Toda cifra de un key_aspect existe en el Markdown de la RFP."""
        from data.pipelines.rfp_intake.graph import is_grounded, run_department_graph

        markdown, metadata = _sample_state(idx)
        out = run_department_graph(ticket_id="t", rfp_id="r", markdown=markdown, metadata=metadata)
        assert out["sections"]
        for section in out["sections"]:
            assert section["key_aspects"]
            for aspect in section["key_aspects"]:
                assert is_grounded(aspect, [markdown]), aspect

    def test_worker_missing_volume_becomes_open_question(self):
        from data.pipelines.rfp_intake.graph import worker

        task = {
            "ticket_id": "t", "rfp_id": "r", "department_id": "warehouse",
            "shared": {"client_country": "Spain", "currency": "EUR", "monthly_volume": None},
            "excerpts": ["Necesitamos almacenamiento en Zaragoza para nuestra marca"],
        }
        section = worker(task)["sections"][0]
        assert not any("Volumen" in a for a in section["key_aspects"])
        assert any("Volumen mensual" in q for q in section["open_questions"])
        assert section["department_owner"] == "Ana Whitfield"
        assert section["approval_status"] == "pendiente"

    def test_is_grounded_rejects_fabricated_figures(self):
        from data.pipelines.rfp_intake.graph import is_grounded

        sources = ["Volumen estimado: 3.000 pedidos al mes."]
        assert is_grounded("Volumen: 3000 pedidos/mes", sources)
        assert not is_grounded("Capacidad: 1200 pallets", sources)

    @needs_samples
    def test_synthesizer_summary_and_handoff(self):
        from data.pipelines.rfp_intake.graph import run_department_graph

        markdown, metadata = _sample_state(2)
        out = run_department_graph(
            ticket_id="t", rfp_id="r", markdown=markdown, metadata=metadata,
            markdown_path="/tmp/t.md",
        )
        summary, handoff = out["sales_summary"], out["handoff"]
        owners = [r["owner"] for r in summary["requests"]]
        assert owners == ["Ana Whitfield", "Carlos Vega"]
        assert "Ana Whitfield" in summary["headline"] and "Carlos Vega" in summary["headline"]
        assert summary["currency"] == "USD"
        assert handoff["next_status"] == "generando_borrador"
        assert handoff["departments"] == ["warehouse", "lastmile"]
        assert handoff["section_ids"] == ["t_warehouse", "t_lastmile"]
        assert handoff["markdown_path"] == "/tmp/t.md"
        assert any("48 horas" in g for g in handoff["guidelines"])


class TestDepartmentWorkers:
    """Unitarios de cada worker: solo usa su subtarea, nunca supuestos."""

    @staticmethod
    def _task(department_id, excerpts, **shared):
        base = {"client_country": "US", "currency": "USD", "monthly_volume": 5000,
                "deadline": "2026-05-15", "budget_range": "USD 40.000"}
        return {"ticket_id": "t", "rfp_id": "r", "department_id": department_id,
                "shared": {**base, **shared}, "excerpts": excerpts}

    def test_lastmile_worker_asks_destinations_and_sla(self):
        from data.pipelines.rfp_intake.graph import worker

        section = worker(self._task("lastmile", ["We need last mile delivery for our brand"]))["sections"][0]
        assert section["department_owner"] == "Carlos Vega"
        assert "Volumen mensual indicado en la RFP: 5000 pedidos/mes" in section["key_aspects"]
        assert any("Destinos" in q for q in section["open_questions"])
        assert any("SLA" in q for q in section["open_questions"])

    def test_lastmile_worker_no_question_when_rfp_states_it(self):
        from data.pipelines.rfp_intake.graph import worker

        excerpts = ["Last mile delivery across US national coverage with next-day SLA"]
        section = worker(self._task("lastmile", excerpts))["sections"][0]
        assert not any("Destinos" in q or "SLA" in q for q in section["open_questions"])

    def test_reverse_worker_flags_48h_guideline_without_inventing(self):
        from data.pipelines.rfp_intake.graph import worker

        section = worker(self._task(
            "reverse", ["Gestión de devoluciones y reacondicionamiento"],
            client_country="Spain", currency="EUR",
        ))["sections"][0]
        assert section["department_owner"] == "Sofía Ramos"
        assert any("48 horas" in q for q in section["open_questions"])
        # El 48 de la política vive en open_questions, nunca en key_aspects.
        assert not any("48" in a for a in section["key_aspects"])

    def test_worker_drops_aspect_with_ungrounded_figure(self, monkeypatch):
        """Si un metadato no está respaldado por las fuentes, no llega a key_aspects."""
        import data.pipelines.rfp_intake.graph as graph_mod

        task = self._task("warehouse", ["Necesitamos almacenamiento en Los Angeles"])
        monkeypatch.setattr(graph_mod, "is_grounded", lambda aspect, _s: "5000" not in aspect)
        section = graph_mod.worker(task)["sections"][0]
        assert not any("5000" in a for a in section["key_aspects"])


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
        meta = sqlite_db.get_rfp_metadata(ticket_id)
        assert meta["currency"] == "USD"
        # Métricas de conversión y costo persistidas junto al ticket.
        assert meta["converter"].startswith("markitdown")
        assert meta["num_pages"] == 1
        assert meta["markdown_path"].endswith(f"{ticket_id}.md")
        assert meta["estimated_tokens"] > 0
        assert meta["processing_cost"] in ("bajo", "medio", "alto")
        assert meta["readability"]["num_words"] > 0
        secs = sqlite_db.get_department_sections(ticket_id)
        assert [s["department_id"] for s in secs] == ["lastmile", "warehouse"]
        assert all(s["key_aspects"] for s in secs)
        assert all(s["source_excerpts"] for s in secs)
        assert all(isinstance(s["open_questions"], list) for s in secs)
        # Synthesizer + handoff persistidos junto al ticket (analisis_completo).
        assert "Carlos Vega" in meta["sales_summary"]["headline"]
        assert meta["handoff"]["next_status"] == "generando_borrador"
        assert sqlite_db.get_ticket(ticket_id)["status"] == "analisis_completo"

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


class TestTicketMode:
    """Recepción en modo ticket: ``register_upload`` + ``analyze_ticket``."""

    def test_register_upload_creates_analizando(self, sqlite_db, tmp_path):
        ticket = register_upload(b"%PDF-1.4 demo", raw_dir=tmp_path)
        assert ticket["status"] == "analizando"
        raw = Path(ticket["raw_pdf_path"])
        assert raw.parent == tmp_path and raw.name == f"{ticket['ticket_id']}.pdf"
        assert raw.read_bytes() == b"%PDF-1.4 demo"
        assert sqlite_db.get_ticket(ticket["ticket_id"])["status"] == "analizando"

    @needs_samples
    def test_analyze_ticket_completes(self, sqlite_db, tmp_path):
        ticket = register_upload(SAMPLE_PDFS[1].read_bytes(), raw_dir=tmp_path)
        received_at = sqlite_db.get_ticket(ticket["ticket_id"])["created_at"]
        result = analyze_ticket(ticket["ticket_id"])
        assert result["status"] == "analisis_completo"
        stored = sqlite_db.get_ticket(ticket["ticket_id"])
        assert stored["status"] == "analisis_completo"
        # created_at es el de la recepción; solo cambia updated_at.
        assert stored["created_at"] == received_at
        assert sqlite_db.get_rfp_metadata(ticket["ticket_id"])["currency"] == "EUR"

    def test_analyze_ticket_error_never_stuck(self, sqlite_db, tmp_path, monkeypatch):
        """Un fallo inesperado deja el ticket descartado, no en analizando."""
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        def _boom(_path):
            raise ValueError("pdf corrupto")

        monkeypatch.setattr(pipeline_mod, "convert_pdf_to_markdown", _boom)
        ticket = register_upload(b"%PDF-1.4 roto", raw_dir=tmp_path)
        result = analyze_ticket(ticket["ticket_id"])
        assert result["status"] == "descartado"
        stored = sqlite_db.get_ticket(ticket["ticket_id"])
        assert stored["status"] == "descartado"
        assert "ValueError" in stored["reason"]


class TestAsyncUploadRoute:
    """POST /rfp/upload responde 202 + analizando; el análisis corre en fondo."""

    @pytest.fixture
    def client(self, sqlite_db, tmp_path, monkeypatch):
        API_DIR = REPO_ROOT / "services" / "api"
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        import data.pipelines.rfp_intake.pipeline as pipeline_mod
        from auth import CurrentUser, get_current_user
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def _upload(self, client, pdf):
        with pdf.open("rb") as handle:
            return client.post(
                "/rfp/upload", files={"file": (pdf.name, handle, "application/pdf")}
            )

    @needs_samples
    @pytest.mark.parametrize("idx,final", [
        (1, "analisis_completo"), (2, "analisis_completo"), (3, "descartado"),
    ])
    def test_upload_returns_analizando_then_final(self, client, tmp_path, idx, final):
        resp = self._upload(client, SAMPLE_PDFS[idx])
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "analizando"
        assert body["filename"] == SAMPLE_PDFS[idx].name
        assert (tmp_path / f"{body['ticket_id']}.pdf").exists()

        # TestClient ejecuta las BackgroundTasks antes de devolver el control.
        detail = client.get(f"/rfp/tickets/{body['ticket_id']}").json()
        assert detail["ticket"]["status"] == final
        if final == "analisis_completo":
            assert detail["departments"]
            assert all(d["key_aspects"] for d in detail["departments"])
            owners = {r["owner"] for r in detail["sales_summary"]["requests"]}
            assert owners == {d["department_owner"] for d in detail["departments"]}
            assert detail["handoff"]["next_status"] == "generando_borrador"
        else:
            assert detail["sales_summary"] is None

    def test_upload_one_ticket_per_pdf(self, client, tmp_path):
        """Dos subidas → dos tickets distintos."""
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4 sin texto")
        ids = {self._upload(client, pdf).json()["ticket_id"] for _ in range(2)}
        assert len(ids) == 2
        assert len(client.get("/rfp/tickets").json()["tickets"]) == 2

    def test_upload_fake_pdf_rejected(self, client, tmp_path):
        """Extensión .pdf sin cabecera %PDF- → 422 y sin ticket."""
        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"hola")
        assert self._upload(client, fake).status_code == 422
        assert client.get("/rfp/tickets").json()["count"] == 0


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

        # Lecturas simuladas; la subida real se prueba en TestAsyncUploadRoute.
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
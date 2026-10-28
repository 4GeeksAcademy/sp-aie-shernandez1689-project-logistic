-- ============================================================================
-- RFP Intake — Hito 9, Parte 1 (trackflow.BRIEFING.md §2.3)
-- Esquema: rfp_intake
--   tickets             — Ticket del intake (estado del documento recibido)
--   rfp_metadata        — Metadatos de la RFP (cliente, país, volumen, legibilidad)
--   department_sections — Sección por departamento activo (key_aspects, aprobación)
--
-- Convenciones del monorepo (ver data/pipelines/migrations/002_reporting_tables.sql):
--   CREATE SCHEMA IF NOT EXISTS / CREATE TABLE IF NOT EXISTS, índices,
--   COMMENT ON TABLE/COLUMN.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS rfp_intake;

-- ── tickets ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rfp_intake.tickets (
    ticket_id     TEXT PRIMARY KEY,
    rfp_id        TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN (
                      'analizando', 'descartado', 'analisis_completo',
                      'generando_borrador', 'en_evaluación', 'needs_human_review',
                      'esperando_aprobación', 'terminado')),
    raw_pdf_path  TEXT NOT NULL,
    reason        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfp_tickets_status
    ON rfp_intake.tickets (status);
CREATE INDEX IF NOT EXISTS idx_rfp_tickets_updated
    ON rfp_intake.tickets (updated_at DESC);

COMMENT ON TABLE rfp_intake.tickets IS
    'Ticket del intake de RFPs (BRIEFING §2.3): estado del documento recibido y ruta del PDF.';
COMMENT ON COLUMN rfp_intake.tickets.status IS
    'Parte 1: analizando | descartado | analisis_completo. Partes 2-3 reservan el resto.';

-- ── rfp_metadata ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rfp_intake.rfp_metadata (
    id                 TEXT PRIMARY KEY,
    ticket_id          TEXT NOT NULL REFERENCES rfp_intake.tickets (ticket_id),
    rfp_id             TEXT NOT NULL,
    doc_type           TEXT,
    client_name        TEXT,
    client_country     TEXT,
    services_requested JSONB NOT NULL DEFAULT '[]'::jsonb,
    monthly_volume     INTEGER,
    deadline           TEXT,
    budget_range       TEXT,
    departments_needed JSONB NOT NULL DEFAULT '[]'::jsonb,
    currency           TEXT,
    num_pages          INTEGER,
    markdown_path      TEXT,
    converter          TEXT,
    estimated_tokens   INTEGER,
    processing_cost    TEXT CHECK (processing_cost IN ('bajo', 'medio', 'alto')),
    readability        JSONB NOT NULL DEFAULT '{}'::jsonb,
    sales_summary      JSONB,
    handoff            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotente para bases donde la tabla ya existía sin estas columnas.
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS doc_type TEXT;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS num_pages INTEGER;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS markdown_path TEXT;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS converter TEXT;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS estimated_tokens INTEGER;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS processing_cost TEXT;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS sales_summary JSONB;
ALTER TABLE rfp_intake.rfp_metadata ADD COLUMN IF NOT EXISTS handoff JSONB;

CREATE INDEX IF NOT EXISTS idx_rfp_metadata_ticket
    ON rfp_intake.rfp_metadata (ticket_id);
CREATE INDEX IF NOT EXISTS idx_rfp_metadata_country
    ON rfp_intake.rfp_metadata (client_country);

COMMENT ON TABLE rfp_intake.rfp_metadata IS
    'Metadatos extraídos de la RFP: cliente, país (moneda), volumen, legibilidad (BRIEFING §2.3).';
COMMENT ON COLUMN rfp_intake.rfp_metadata.markdown_path IS
    'Markdown convertido con MarkItDown (data/raw/rfp/<ticket_id>.md): lo que leen clasificador y agentes.';
COMMENT ON COLUMN rfp_intake.rfp_metadata.processing_cost IS
    'Costo anticipado de procesamiento (bajo|medio|alto) por tokens estimados y densidad del texto.';
COMMENT ON COLUMN rfp_intake.rfp_metadata.readability IS
    'Volumen (chars, palabras, frases, tokens estimados) + índices py-readability-metrics (null si < 100 palabras).';
COMMENT ON COLUMN rfp_intake.rfp_metadata.sales_summary IS
    'Synthesizer: resumen para Ventas — qué pedir a cada responsable y qué preguntar al cliente.';
COMMENT ON COLUMN rfp_intake.rfp_metadata.handoff IS
    'Handoff a la Parte 2: next_status=generando_borrador, departamentos, secciones, moneda, lineamientos §5.';
COMMENT ON COLUMN rfp_intake.rfp_metadata.currency IS
    'Moneda de la propuesta según país (BRIEFING §5: US→USD, España→EUR).';

-- ── department_sections ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rfp_intake.department_sections (
    id                 TEXT PRIMARY KEY,
    ticket_id          TEXT NOT NULL REFERENCES rfp_intake.tickets (ticket_id),
    rfp_id             TEXT NOT NULL,
    department_id      TEXT NOT NULL CHECK (department_id IN ('warehouse','lastmile','reverse')),
    department_label   TEXT,
    department_owner   TEXT,
    key_aspects        JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_excerpts    JSONB NOT NULL DEFAULT '[]'::jsonb,
    open_questions     JSONB NOT NULL DEFAULT '[]'::jsonb,
    draft_content      TEXT,
    evaluation_results JSONB,
    approval_status    TEXT NOT NULL DEFAULT 'pendiente',
    approver           TEXT,
    approved_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS source_excerpts JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS open_questions JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_rfp_department_ticket
    ON rfp_intake.department_sections (ticket_id);
CREATE INDEX IF NOT EXISTS idx_rfp_department_dep
    ON rfp_intake.department_sections (department_id);

COMMENT ON TABLE rfp_intake.department_sections IS
    'Sección por departamento activo: key_aspects (Parte 1), draft_content (Parte 2), aprobación (Parte 3).';
COMMENT ON COLUMN rfp_intake.department_sections.key_aspects IS
    'Aspectos clave del worker del departamento: solo datos presentes en la RFP (cifras verificadas contra los extractos).';
COMMENT ON COLUMN rfp_intake.department_sections.open_questions IS
    'Información que el departamento necesita y la RFP no indica (preguntas para el cliente; nunca supuestos).';
COMMENT ON COLUMN rfp_intake.department_sections.approval_status IS
    'Flujo de aprobación (Parte 3). Parte 1: siempre "pendiente".';
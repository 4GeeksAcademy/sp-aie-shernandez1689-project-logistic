-- ============================================================================
-- RFP Intake — Hito 9, Parte 3 (trackflow.BRIEFING.md §2.3, §6, §7)
-- Aprobación humana por departamento, arbitraje y documento final.
--   department_sections — decisión del responsable, comentarios, rondas, historial
--   rfp_metadata        — estado de la aprobación (conflictos, arbitrajes, trace)
--   final_documents     — FinalDocument: ticket_id, sections, currency, generated_at
--
-- El estado reanudable de los grafos vive en las tablas del checkpointer de
-- LangGraph (PostgresSaver.setup(), threads rfp-{ticket_id}[:{department}]).
--
-- Idempotente: se aplica después de 002_proposal_generation.sql.
-- ============================================================================

ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS approval_comments TEXT;
ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS approval_rounds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS approval_history JSONB;

ALTER TABLE rfp_intake.department_sections
    DROP CONSTRAINT IF EXISTS department_sections_approval_status_check;
ALTER TABLE rfp_intake.department_sections
    ADD CONSTRAINT department_sections_approval_status_check
    CHECK (approval_status IN ('pendiente', 'cambios_solicitados', 'aprobada', 'rechazada'));

ALTER TABLE rfp_intake.rfp_metadata
    ADD COLUMN IF NOT EXISTS approval JSONB;

CREATE TABLE IF NOT EXISTS rfp_intake.final_documents (
    ticket_id    TEXT PRIMARY KEY REFERENCES rfp_intake.tickets (ticket_id),
    rfp_id       TEXT NOT NULL,
    currency     TEXT CHECK (currency IS NULL OR currency IN ('USD', 'EUR')),
    sections     JSONB NOT NULL DEFAULT '[]'::jsonb,
    content      TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfp_department_approval
    ON rfp_intake.department_sections (approval_status);

COMMENT ON COLUMN rfp_intake.department_sections.approval_status IS
    'pendiente | cambios_solicitados | aprobada | rechazada (Parte 3).';
COMMENT ON COLUMN rfp_intake.department_sections.approval_comments IS
    'Comentario de la última decisión (obligatorio al rechazar o pedir cambios).';
COMMENT ON COLUMN rfp_intake.department_sections.approval_rounds IS
    'Reescrituras por request_changes (humano o arbitraje); límite RFP_MAX_APPROVAL_ROUNDS.';
COMMENT ON COLUMN rfp_intake.department_sections.approval_history IS
    'Decisiones: [{decision, approver, comments, source human|arbitration, triggers, decided_at, round}].';
COMMENT ON COLUMN rfp_intake.rfp_metadata.approval IS
    'Estado de la Parte 3: departamentos, conflictos §7, arbitrajes y trace (agente, input, output, timestamp).';
COMMENT ON TABLE rfp_intake.final_documents IS
    'Documento final: secciones aprobadas consolidadas en la moneda del país del cliente.';

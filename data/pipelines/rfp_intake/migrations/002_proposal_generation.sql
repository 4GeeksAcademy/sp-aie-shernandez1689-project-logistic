-- ============================================================================
-- RFP Intake — Hito 9, Parte 2 (trackflow.BRIEFING.md §2.3, §5)
-- Generación de secciones + ciclo generador-evaluador.
--   department_sections — borradores, EvaluationResult por iteración, estado
--   rfp_metadata        — handoff de la Parte 2 hacia la Parte 3
--
-- Idempotente: se aplica después de 001_rfp_intake.sql.
-- ============================================================================

ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS drafts JSONB;
ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS generation_status TEXT;
ALTER TABLE rfp_intake.department_sections
    ADD COLUMN IF NOT EXISTS iterations INTEGER NOT NULL DEFAULT 0;

ALTER TABLE rfp_intake.department_sections
    DROP CONSTRAINT IF EXISTS department_sections_generation_status_check;
ALTER TABLE rfp_intake.department_sections
    ADD CONSTRAINT department_sections_generation_status_check
    CHECK (generation_status IS NULL OR generation_status IN (
        'generando_borrador', 'en_evaluación', 'evaluacion_superada', 'needs_human_review'));

ALTER TABLE rfp_intake.rfp_metadata
    ADD COLUMN IF NOT EXISTS proposal_handoff JSONB;

CREATE INDEX IF NOT EXISTS idx_rfp_department_generation
    ON rfp_intake.department_sections (generation_status);

COMMENT ON COLUMN rfp_intake.department_sections.draft_content IS
    'Último borrador de la sección de propuesta económica (Parte 2).';
COMMENT ON COLUMN rfp_intake.department_sections.drafts IS
    'Historial de borradores: [{iteration, content, generator, generated_at}].';
COMMENT ON COLUMN rfp_intake.department_sections.evaluation_results IS
    'EvaluationResult por iteración: readability, relevance, compliance, overall_pass, feedback_for_generator.';
COMMENT ON COLUMN rfp_intake.department_sections.generation_status IS
    'generando_borrador | en_evaluación | evaluacion_superada | needs_human_review (límite agotado).';
COMMENT ON COLUMN rfp_intake.department_sections.iterations IS
    'Iteraciones del ciclo generador-evaluador consumidas (KPI §3: ideal < 2).';
COMMENT ON COLUMN rfp_intake.rfp_metadata.proposal_handoff IS
    'Handoff a la Parte 3: secciones con último borrador + EvaluationResult, needs_human_review incluidas.';

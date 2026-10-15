import Link from "next/link";
import dynamic from "next/dynamic";
import { notFound } from "next/navigation";
import {
  formatDate,
  getCandidate,
  stageLabels,
  statusLabels,
} from "@/lib/candidates";
import { CandidateProgress } from "@/components/candidate-controls";

/**
 * CandidateForm se carga de forma diferida porque está debajo del pliegue
 * en la página de detalle. El formulario de edición es contenido secundario
 * frente a la información principal (datos, progreso, estado). Diferirlo
 * permite que el layout crítico se renderice sin esperar a este componente.
 */
const DynamicCandidateForm = dynamic(() => import("@/components/candidate-form"), {
  loading: () => <p role="status">Cargando formulario...</p>,
});

/**
 * NotesSection se carga de forma diferida porque:
 * 1. Las notas requieren su propia llamada API (GET /records/:id/notes)
 * 2. Son contenido secundario que aparece debajo del pliegue
 * 3. El usuario obtiene la información principal inmediatamente
 *    sin bloquearse esperando a las notas
 */
const DynamicNotesSection = dynamic(() => import("@/components/notes-section"), {
  loading: () => <p role="status">Cargando notas...</p>,
});

export default async function CandidateDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const candidate = await getCandidate(id);
  if (!candidate) notFound();

  return (
    <main className="workspace">
      <Link className="back-link" href="/">Volver a candidaturas</Link>
      <header className="page-heading detail-heading">
        <div>
          <p className="eyebrow">Detalle de candidatura</p>
          <h1>{candidate.full_name || "Nombre no disponible"}</h1>
          <p className="subtitle">{candidate.position || "Puesto no especificado"}</p>
        </div>
        <span className={`status status-${candidate.status}`}>
          {statusLabels[candidate.status] ?? "Sin estado"}
        </span>
      </header>
      <div className="detail-grid">
        <section aria-labelledby="candidate-data">
          <h2 id="candidate-data">Datos de la candidatura</h2>
          <CandidateProgress candidate={candidate} />
          <DynamicCandidateForm candidate={candidate} />
          <dl className="candidate-data">
            <div><dt>Estado</dt><dd>{statusLabels[candidate.status] ?? "Sin estado"}</dd></div>
            <div><dt>Etapa</dt><dd>{stageLabels[candidate.stage] ?? "Sin etapa"}</dd></div>
            <div><dt>Fecha de candidatura</dt><dd><time dateTime={candidate.applied_at}>{formatDate(candidate.applied_at)}</time></dd></div>
            <div><dt>Última actualización</dt><dd><time dateTime={candidate.updated_at}>{formatDate(candidate.updated_at)}</time></dd></div>
            <div><dt>Identificador</dt><dd className="record-id">{candidate.id}</dd></div>
            <div><dt>Notas registradas</dt><dd>{candidate.notes_count}</dd></div>
          </dl>
        </section>
        <DynamicNotesSection id={candidate.id} />
      </div>
    </main>
  );
}
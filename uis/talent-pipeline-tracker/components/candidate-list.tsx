"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { stageLabels, statusLabels } from "@/lib/candidates";
import type { Candidate } from "@/types/candidates";

function isKeyOf<T extends object>(labels: T, value: string | null): value is Extract<keyof T, string> {
  return value !== null && Object.hasOwn(labels, value);
}

export default function CandidateList({ candidates }: { candidates: Candidate[] }) {
  const searchParams = useSearchParams();
  const rawStatus = searchParams.get("status");
  const rawStage = searchParams.get("stage");
  const status = isKeyOf(statusLabels, rawStatus) ? rawStatus : "";
  const stage = isKeyOf(stageLabels, rawStage) ? rawStage : "";
  const query = searchParams.get("q") ?? "";

  // History API keeps useSearchParams in sync without a server round-trip or reload.
  function updateParam(key: string, value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(key, value);
    else params.delete(key);
    const search = params.toString();
    window.history.replaceState(null, "", search ? `?${search}` : window.location.pathname);
  }

  const term = query.trim().toLowerCase();
  const filtered = candidates.filter((candidate) =>
    (!status || candidate.status === status) &&
    (!stage || candidate.stage === stage) &&
    (!term ||
      candidate.full_name.toLowerCase().includes(term) ||
      candidate.email.toLowerCase().includes(term)),
  );

  return (
    <>
      <form className="filters" role="search" onSubmit={(event) => event.preventDefault()}>
        <label>
          <span>Buscar</span>
          <input
            type="search"
            placeholder="Nombre o email"
            value={query}
            onChange={(event) => updateParam("q", event.target.value)}
          />
        </label>
        <label>
          <span>Estado</span>
          <select value={status} onChange={(event) => updateParam("status", event.target.value)}>
            <option value="">Todos</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Etapa</span>
          <select value={stage} onChange={(event) => updateParam("stage", event.target.value)}>
            <option value="">Todas</option>
            {Object.entries(stageLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
      </form>
      <p className="record-count" role="status">
        {filtered.length} de {candidates.length} candidaturas
      </p>
      {filtered.length === 0 ? (
        <p className="empty-state">
          {candidates.length === 0 ? "Todavía no hay candidaturas." : "Ninguna candidatura coincide con los filtros."}
        </p>
      ) : (
        <div className="table-scroll">
          <table>
            <caption className="sr-only">Candidaturas de TrackFlow</caption>
            <thead>
              <tr>
                <th scope="col">Candidato</th>
                <th scope="col">Puesto</th>
                <th scope="col">Estado</th>
                <th scope="col">Etapa</th>
                <th scope="col"><span className="sr-only">Detalle</span></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((candidate) => (
                <tr key={candidate.id}>
                  <td>
                    <Link className="candidate-name" href={`/candidates/${candidate.id}`}>
                      {candidate.full_name}
                    </Link>
                    <span className="secondary-text">{candidate.email}</span>
                  </td>
                  <td>{candidate.position}</td>
                  <td><span className={`status status-${candidate.status}`}>{statusLabels[candidate.status] ?? "Sin estado"}</span></td>
                  <td>{stageLabels[candidate.stage] ?? "Sin etapa"}</td>
                  <td>
                    <Link className="detail-link" href={`/candidates/${candidate.id}`} aria-label={`Ver candidatura de ${candidate.full_name}`}>
                      Ver detalle
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

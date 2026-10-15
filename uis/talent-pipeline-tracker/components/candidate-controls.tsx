"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  stageLabels,
  statusLabels,
  updateCandidateProgress,
} from "@/lib/candidates";
import type { Candidate } from "@/types/candidates";

export function CandidateProgress({ candidate }: { candidate: Candidate }) {
  const router = useRouter();
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  async function update(change: { status: Candidate["status"] } | { stage: Candidate["stage"] }) {
    setError("");
    setMessage("");
    setIsPending(true);
    try {
      await updateCandidateProgress(candidate.id, change);
      setMessage("Candidatura actualizada.");
      router.refresh();
    } catch {
      setError("No se pudo actualizar la candidatura. Vuelve a intentarlo o contacta con soporte.");
    } finally {
      setIsPending(false);
    }
  }

  return (
    <div className="progress-controls" aria-busy={isPending}>
      <fieldset disabled={isPending}>
        <legend>Seguimiento</legend>
        <label>
          Estado
          <select
            value={candidate.status}
            onChange={(event) => update({ status: event.target.value as Candidate["status"] })}
          >
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Etapa
          <select
            value={candidate.stage}
            onChange={(event) => update({ stage: event.target.value as Candidate["stage"] })}
          >
            {Object.entries(stageLabels).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
      </fieldset>
      <p className="action-message" role="status">{isPending ? "Guardando cambios..." : message}</p>
      {error && <p className="action-error" role="alert">{error}</p>}
    </div>
  );
}
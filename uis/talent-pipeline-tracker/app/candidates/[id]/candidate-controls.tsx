"use client";

import { useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  addCandidateNote,
  deleteCandidateNote,
  formatDate,
  stageLabels,
  statusLabels,
  updateCandidateProgress,
  type Candidate,
  type CandidateNote,
} from "@/lib/candidates";

export function CandidateProgress({ candidate }: { candidate: Candidate }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function update(change: { status: Candidate["status"] } | { stage: Candidate["stage"] }) {
    setError("");
    setMessage("");
    startTransition(async () => {
      try {
        await updateCandidateProgress(candidate.id, change);
        setMessage("Candidatura actualizada.");
        router.refresh();
      } catch {
        setError("No se pudo actualizar la candidatura. Vuelve a intentarlo.");
      }
    });
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

export function CandidateNotes({ id, notes }: { id: string; notes: CandidateNote[] }) {
  const router = useRouter();
  const [content, setContent] = useState("");
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isPending || !content.trim()) return;
    setError("");
    setMessage("");
    startTransition(async () => {
      try {
        await addCandidateNote(id, content);
        setContent("");
        setMessage("Nota añadida.");
        router.refresh();
      } catch {
        setError("No se pudo añadir la nota. El texto se ha conservado; vuelve a intentarlo.");
      }
    });
  }

  function deleteNote(noteId: string) {
    if (isPending || !window.confirm("¿Eliminar esta nota? Esta acción no se puede deshacer.")) return;
    setError("");
    setMessage("");
    startTransition(async () => {
      try {
        await deleteCandidateNote(id, noteId);
        setMessage("Nota eliminada.");
        router.refresh();
      } catch {
        setError("No se pudo eliminar la nota. Vuelve a intentarlo.");
      }
    });
  }

  return (
    <section className="notes-section" aria-labelledby="candidate-notes" aria-busy={isPending}>
      <h2 id="candidate-notes">Notas internas <span className="secondary-count">{notes.length}</span></h2>
      <form className="note-form" onSubmit={addNote}>
        <label htmlFor="new-note">Nueva nota</label>
        <textarea
          id="new-note"
          rows={4}
          required
          value={content}
          disabled={isPending}
          onChange={(event) => setContent(event.target.value)}
        />
        <button type="submit" disabled={isPending || !content.trim()}>Añadir nota</button>
      </form>
      <p className="action-message" role="status">{isPending ? "Guardando cambios..." : message}</p>
      {error && <p className="action-error" role="alert">{error}</p>}
      {notes.length === 0 ? (
        <p className="empty-state">Esta candidatura no tiene notas.</p>
      ) : (
        <ol className="notes-list">
          {notes.map((note) => (
            <li key={note.id}>
              <time dateTime={note.created_at}>{formatDate(note.created_at)}</time>
              <p>{note.content}</p>
              <button
                className="delete-note"
                type="button"
                disabled={isPending}
                onClick={() => deleteNote(note.id)}
              >
                Eliminar nota
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
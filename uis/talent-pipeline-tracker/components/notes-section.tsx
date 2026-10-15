"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  addCandidateNote,
  deleteCandidateNote,
  formatDate,
  getCandidateNotes,
} from "@/lib/candidates";
import type { CandidateNote } from "@/types/candidates";

/**
 * Sección de notas autocontenida para lazy loading.
 *
 * Carga las notas de forma completamente asíncrona sin bloquear
 * el renderizado de la página principal de detalle de candidatura.
 * El componente se encarga de su propio fetching, estado de carga
 * y manejo de errores.
 *
 * Justificación de lazy loading:
 * - Las notas son contenido secundario que aparece debajo del pliegue
 * - Requieren una llamada API independiente (GET /records/:id/notes)
 * - La información principal (datos del candidato, progreso y estado)
 *   debe mostrarse inmediatamente sin esperar a las notas
 */
export default function NotesSection({ id }: { id: string }) {
  const router = useRouter();
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [content, setContent] = useState("");
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // Carga diferida de notas sin bloquear el layout principal
  useEffect(() => {
    let active = true;
    getCandidateNotes(id)
      .then((data) => {
        if (active) {
          setNotes(data);
          setLoading(false);
        }
      })
      .catch(() => {
        if (active) {
          setLoadError("No se pudieron cargar las notas.");
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [id]);

  async function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isPending || !content.trim()) return;
    setError("");
    setMessage("");
    setIsPending(true);
    try {
      await addCandidateNote(id, content);
      setContent("");
      setMessage("Nota añadida.");
      router.refresh();
    } catch {
      setError(
        "No se pudo añadir la nota. El texto se ha conservado; vuelve a intentarlo o contacta con soporte.",
      );
    } finally {
      setIsPending(false);
    }
  }

  async function deleteNote(noteId: string) {
    if (
      isPending ||
      !window.confirm("¿Eliminar esta nota? Esta acción no se puede deshacer.")
    )
      return;
    setError("");
    setMessage("");
    setIsPending(true);
    try {
      await deleteCandidateNote(id, noteId);
      setMessage("Nota eliminada.");
      router.refresh();
    } catch {
      setError(
        "No se pudo eliminar la nota. Vuelve a intentarlo o contacta con soporte.",
      );
    } finally {
      setIsPending(false);
    }
  }

  return (
    <section
      className="notes-section"
      aria-labelledby="candidate-notes"
      aria-busy={isPending || loading}
    >
      <h2 id="candidate-notes">
        Notas internas
        {!loading && <span className="secondary-count">{notes.length}</span>}
      </h2>

      {loading ? (
        <p role="status">Cargando notas...</p>
      ) : loadError ? (
        <p className="action-error" role="alert">
          {loadError}
        </p>
      ) : (
        <>
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
            <button type="submit" disabled={isPending || !content.trim()}>
              Añadir nota
            </button>
          </form>
          <p className="action-message" role="status">
            {isPending ? "Guardando cambios..." : message}
          </p>
          {error && (
            <p className="action-error" role="alert">
              {error}
            </p>
          )}
          {notes.length === 0 ? (
            <p className="empty-state">Esta candidatura no tiene notas.</p>
          ) : (
            <ol className="notes-list">
              {notes.map((note) => (
                <li key={note.id}>
                  <time dateTime={note?.created_at ?? ""}>
                    {formatDate(note?.created_at)}
                  </time>
                  <p>{note?.content ?? "Contenido no disponible"}</p>
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
        </>
      )}
    </section>
  );
}
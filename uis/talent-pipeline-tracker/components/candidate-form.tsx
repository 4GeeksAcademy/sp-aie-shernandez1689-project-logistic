"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import {
  createCandidate,
  updateCandidate,
} from "@/lib/candidates";
import type { Candidate, CandidateInput } from "@/types/candidates";

type CandidateFormProps = {
  candidate?: Candidate;
};

export default function CandidateForm({ candidate }: CandidateFormProps) {
  const router = useRouter();
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [createdCandidate, setCreatedCandidate] = useState<Candidate | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isPending || createdCandidate) return;

    const form = event.currentTarget;
    const requiredInputs = form.querySelectorAll<HTMLInputElement>("input[required]");
    for (const input of requiredInputs) {
      input.setCustomValidity(input.value.trim() ? "" : "Este campo es obligatorio.");
    }
    if (!form.reportValidity()) return;

    const formData = new FormData(form);
    const input: CandidateInput = {
      full_name: String(formData.get("full_name")).trim(),
      email: String(formData.get("email")).trim(),
      phone: String(formData.get("phone")).trim(),
      position: String(formData.get("position")).trim(),
      experience_years: Number(formData.get("experience_years")),
      linkedin_url: String(formData.get("linkedin_url")).trim() || null,
      cv_url: String(formData.get("cv_url")).trim() || null,
    };

    setError("");
    setMessage("");
    setIsPending(true);
    try {
      if (candidate) {
        await updateCandidate(candidate.id, input);
        setMessage("Datos de la candidatura actualizados correctamente.");
      } else {
        const created = await createCandidate(input);
        setCreatedCandidate(created);
        setMessage("Candidatura registrada correctamente.");
      }
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo guardar la candidatura.");
    } finally {
      setIsPending(false);
    }
  }

  return (
    <form
      className="candidate-form"
      onSubmit={submit}
      onInput={(event) => {
        if (event.target instanceof HTMLInputElement) event.target.setCustomValidity("");
      }}
      aria-busy={isPending}
    >
      <h2>{candidate ? "Editar datos de la candidatura" : "Nueva candidatura"}</h2>
      <div className="candidate-form-grid">
        <label>
          Nombre completo <span aria-hidden="true">*</span>
          <input name="full_name" required defaultValue={candidate?.full_name ?? ""} />
        </label>
        <label>
          Email <span aria-hidden="true">*</span>
          <input name="email" type="email" required defaultValue={candidate?.email ?? ""} />
        </label>
        <label>
          Teléfono <span aria-hidden="true">*</span>
          <input name="phone" type="tel" required defaultValue={candidate?.phone ?? ""} />
        </label>
        <label>
          Puesto <span aria-hidden="true">*</span>
          <input name="position" required defaultValue={candidate?.position ?? ""} />
        </label>
        <label>
          Años de experiencia <span aria-hidden="true">*</span>
          <input
            name="experience_years"
            type="number"
            required
            step="any"
            defaultValue={candidate?.experience_years ?? ""}
          />
        </label>
        <label>
          LinkedIn
          <input name="linkedin_url" type="url" placeholder="https://..." defaultValue={candidate?.linkedin_url ?? ""} />
        </label>
        <label className="candidate-form-wide">
          Currículum (URL)
          <input name="cv_url" type="url" placeholder="https://..." defaultValue={candidate?.cv_url ?? ""} />
        </label>
      </div>
      <p className="form-hint">Los campos marcados con * son obligatorios.</p>
      <button type="submit" disabled={isPending || !!createdCandidate}>
        {isPending ? "Guardando..." : candidate ? "Guardar cambios" : "Registrar candidatura"}
      </button>
      <p className="action-message" role="status">{message}</p>
      {createdCandidate && (
        <Link className="detail-link" href={`/candidates/${encodeURIComponent(createdCandidate.id)}`}>
          Ver candidatura registrada
        </Link>
      )}
      {error && <p className="action-error" role="alert">{error}</p>}
    </form>
  );
}
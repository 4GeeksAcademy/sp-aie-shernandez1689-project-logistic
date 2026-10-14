"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const data = new FormData(event.currentTarget);
    setSaving(true);
    setError("");
    try {
      await login(String(data.get("email") ?? "").trim(), String(data.get("password") ?? ""));
      router.replace("/backoffice/inventory/orders/inbound");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo iniciar sesión.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="workspace">
      <header className="page-heading"><div><p className="eyebrow">TrackFlow</p><h1>Iniciar sesión</h1></div></header>
      {error && <p role="alert" className="action-error">{error}</p>}
      <form className="candidate-form max-w-xl" onSubmit={submit}>
        <fieldset className="candidate-form-grid" disabled={saving}>
          <legend className="sr-only">Credenciales</legend>
          <label className="candidate-form-wide">Email<input name="email" type="email" autoComplete="username" required /></label>
          <label className="candidate-form-wide">Contraseña<input name="password" type="password" autoComplete="current-password" required /></label>
          <button type="submit" className="candidate-form-wide" disabled={saving}>{saving ? "Ingresando..." : "Iniciar sesión"}</button>
        </fieldset>
      </form>
    </main>
  );
}
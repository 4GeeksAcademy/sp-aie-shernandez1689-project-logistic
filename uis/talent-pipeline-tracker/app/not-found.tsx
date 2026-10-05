import Link from "next/link";

export default function NotFound() {
  return (
    <main className="workspace">
      <p className="eyebrow">Candidatura no encontrada</p>
      <h1>Esta candidatura no existe</h1>
      <p className="subtitle">Puede que se haya eliminado o que el enlace sea incorrecto.</p>
      <Link className="back-link" href="/">Volver a candidaturas</Link>
    </main>
  );
}
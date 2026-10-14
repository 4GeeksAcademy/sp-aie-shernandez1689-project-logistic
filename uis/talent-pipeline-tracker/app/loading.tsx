export default function Loading() {
  return (
    <main className="workspace loading-state" aria-busy="true">
      <span className="loading-spinner" aria-hidden="true" />
      <p role="status">Cargando candidaturas…</p>
    </main>
  );
}
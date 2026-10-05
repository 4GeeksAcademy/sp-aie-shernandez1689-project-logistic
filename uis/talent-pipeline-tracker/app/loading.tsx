export default function Loading() {
  return (
    <main className="workspace" aria-busy="true">
      <p className="empty-state" role="status">Cargando candidaturas…</p>
    </main>
  );
}
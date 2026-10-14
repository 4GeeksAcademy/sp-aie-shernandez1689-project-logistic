"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { clearAccessToken } from "@/lib/auth";
import { getOrders, InventoryApiError, type StockMovement } from "@/lib/inventory";

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

export default function OrdersPage() {
  const router = useRouter();
  const [orders, setOrders] = useState<StockMovement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getOrders()
      .then((data) => { if (active) setOrders(data); })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof InventoryApiError && reason.status === 401) {
          clearAccessToken();
          router.replace("/login");
          return;
        }
        setError(reason instanceof Error ? reason.message : "No se pudo cargar el historial de movimientos de stock.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt, router]);

  function retry() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  return (
    <main className="workspace">
      <header className="page-heading"><div><p className="eyebrow">Inventario · TrackFlow</p><h1>Historial de movimientos de stock</h1></div></header>
      {loading && <p role="status" className="empty-state">Cargando movimientos de stock...</p>}
      {error && <div role="alert"><p className="action-error">{error}</p><button type="button" onClick={retry}>Reintentar</button></div>}
      {!loading && !error && (
        <>
          <p className="record-count mb-3">{orders.length} movimientos de stock</p>
          {orders.length === 0 ? <p className="empty-state">No hay movimientos de stock registrados.</p> : (
            <div className="table-scroll" role="region" aria-label="Historial de movimientos de stock" tabIndex={0}>
              <table className="orders-table">
                <caption className="sr-only">Movimientos de entrada y salida de inventario</caption>
                <thead><tr>{["Nombre del SKU", "Unidades", "Movimiento de stock", "Fecha de creación", "Operario / Coordinador (user_uuid)"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
                <tbody>{orders.map((order) => (
                  <tr key={`${order.order_type}-${order.id}`}>
                    <td className="break-words">{order.product.name}<span className="secondary-text">{order.product.sku} · {order.warehouse}</span></td>
                    <td className="tabular-nums">{order.quantity}</td>
                    <td><span className={`status ${order.order_type === "inbound" ? "status-selected" : order.exit_type === "loss" ? "status-discarded" : "status-in_progress"}`}>{order.order_type === "inbound" ? "Recepción (entrada)" : order.exit_type === "loss" ? "Pérdida (salida)" : "Despacho (salida)"}</span></td>
                    <td><time dateTime={order.created_at}>{formatDate(order.created_at)}</time></td>
                    <td className="record-id break-all">{order.user_uuid}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
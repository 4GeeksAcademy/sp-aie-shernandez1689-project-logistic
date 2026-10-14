"use client";

import { useEffect, useState } from "react";
import { getOrders, type InventoryOrder } from "@/lib/inventory";

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

export default function OrdersPage() {
  const [orders, setOrders] = useState<InventoryOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getOrders()
      .then((data) => { if (active) setOrders(data); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "No se pudo cargar el historial de órdenes.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);

  function retry() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  return (
    <main className="workspace">
      <header className="page-heading"><div><p className="eyebrow">Inventario · TrackFlow</p><h1>Historial de órdenes</h1></div></header>
      {loading && <p role="status" className="empty-state">Cargando historial de órdenes...</p>}
      {error && <div role="alert"><p className="action-error">{error}</p><button type="button" onClick={retry}>Reintentar</button></div>}
      {!loading && !error && (
        <>
          <p className="record-count mb-3">{orders.length} órdenes</p>
          {orders.length === 0 ? <p className="empty-state">No hay órdenes registradas.</p> : (
            <div className="table-scroll" role="region" aria-label="Historial de órdenes" tabIndex={0}>
              <table className="orders-table">
                <caption className="sr-only">Movimientos de entrada y salida de inventario</caption>
                <thead><tr>{["Producto", "Cantidad", "Tipo de orden", "Fecha de creación", "Usuario (user_uuid)"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
                <tbody>{orders.map((order) => (
                  <tr key={`${order.order_type}-${order.id}`}>
                    <td className="break-words">{order.product.name}<span className="secondary-text">{order.product.sku} · {order.warehouse}</span></td>
                    <td className="tabular-nums">{order.quantity}</td>
                    <td><span className={`status ${order.order_type === "inbound" ? "status-selected" : "status-in_progress"}`}>{order.order_type === "inbound" ? "Entrada" : "Salida"}</span></td>
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
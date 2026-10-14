"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getProducts, type Product } from "@/lib/inventory";

const categoryLabels = { fashion: "Moda", electronics: "Electrónica", cosmetics: "Cosmética" };
const warehouseLabels = { LA: "Los Ángeles (LA)", ZGZ: "Zaragoza (ZGZ)" };

// Stock por almacén recibido de la API: <= 0 agotado, 1-10 bajo, > 10 saludable.
function stockLevel(stock: number) {
  if (stock <= 0) return { label: "Agotado", className: "status-discarded" };
  if (stock <= 10) return { label: "Bajo", className: "status-in_progress" };
  return { label: "Saludable", className: "status-selected" };
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getProducts()
      .then((data) => { if (active) setProducts(data); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "No se pudieron cargar los productos.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);

  function reload() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  return (
    <main className="workspace">
      <header className="page-heading">
        <div><p className="eyebrow">Inventario · TrackFlow</p><h1>Productos</h1></div>
        <button type="button" onClick={reload} disabled={loading}>Actualizar productos</button>
      </header>
      {loading && <p role="status" className="empty-state">Cargando productos...</p>}
      {error && <div role="alert"><p className="action-error">{error}</p><button type="button" onClick={reload}>Reintentar</button></div>}
      {!loading && !error && (
        <>
          <p className="record-count mb-3">{products.length} productos</p>
          {products.length === 0 ? <p className="empty-state">No hay productos registrados.</p> : (
            <div className="table-scroll" role="region" aria-label="Productos por almacén" tabIndex={0}>
              <table>
                <caption className="sr-only">Productos y stock actual de su almacén</caption>
                <thead><tr>{["ID / SKU", "Producto", "Cliente", "Categoría", "Almacén", "Stock actual", "Órdenes"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{products.map((product) => {
                  const level = stockLevel(product.current_stock);
                  return (
                    <tr key={product.id}>
                      <td><span className="record-id">#{product.id}</span><span className="secondary-text">{product.sku}</span></td>
                      <td className="break-words">{product.name}</td>
                      <td>{product.client_name}</td>
                      <td>{categoryLabels[product.category] ?? product.category}</td>
                      <td>{warehouseLabels[product.warehouse] ?? product.warehouse}</td>
                      <td><strong className="block tabular-nums">{product.current_stock}</strong><span className={`status ${level.className}`}>{level.label}</span></td>
                      <td>
                        <div className="flex flex-col gap-2 min-w-36">
                          <Link href={`/backoffice/inventory/orders/inbound?sku_id=${product.id}`} aria-label={`Crear entrada para ${product.sku}`}>Crear entrada</Link>
                          <Link href={`/backoffice/inventory/orders/outbound?sku_id=${product.id}`} aria-label={`Crear salida para ${product.sku}`}>Crear salida</Link>
                        </div>
                      </td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
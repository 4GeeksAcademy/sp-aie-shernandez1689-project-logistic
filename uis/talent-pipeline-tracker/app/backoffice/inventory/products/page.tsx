"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { clearAccessToken } from "@/lib/auth";
import { getProducts, InventoryApiError, type SKU } from "@/lib/inventory";

const categoryLabels = { fashion: "Moda", electronics: "Electrónica", cosmetics: "Cosmética" };
const warehouseLabels = { LA: "Los Ángeles (LA)", ZGZ: "Zaragoza (ZGZ)" };

// Stock por almacén recibido de la API: <= 0 agotado, 1-10 bajo, > 10 saludable.
function stockLevel(stock: number) {
  if (stock <= 0) return { label: "Agotado", className: "status-discarded" };
  if (stock <= 10) return { label: "Bajo", className: "status-in_progress" };
  return { label: "Saludable", className: "status-selected" };
}

export default function ProductsPage() {
  const router = useRouter();
  const [products, setProducts] = useState<SKU[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    getProducts()
      .then((data) => { if (active) setProducts(data); })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof InventoryApiError && reason.status === 401) {
          clearAccessToken();
          router.replace("/login");
          return;
        }
        setError(reason instanceof Error ? reason.message : "No se pudieron cargar los SKUs.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt, router]);

  function reload() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  return (
    <main className="workspace">
      <header className="page-heading">
        <div><p className="eyebrow">Inventario · TrackFlow</p><h1>SKU</h1></div>
        <button type="button" onClick={reload} disabled={loading}>Actualizar SKUs</button>
      </header>
      {loading && <p role="status" className="empty-state">Cargando SKUs...</p>}
      {error && <div role="alert"><p className="action-error">{error}</p><button type="button" onClick={reload}>Reintentar</button></div>}
      {!loading && !error && (
        <>
          <p className="record-count mb-3">{products.length} SKUs</p>
          {products.length === 0 ? <p className="empty-state">No hay SKUs registrados.</p> : (
            <div className="table-scroll" role="region" aria-label="SKUs por almacén" tabIndex={0}>
              <table>
                <caption className="sr-only">SKUs y stock actual de su almacén</caption>
                <thead><tr>{["ID / Código SKU", "Nombre del SKU", "Marca cliente", "Categoría", "Almacén", "Stock actual", "Movimientos de stock"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
                {/*
                  Los datos enriquecidos se calculan con useMemo porque:
                  - stockLevel() es O(1) pero se combina con búsquedas en
                    categoryLabels y warehouseLabels por cada producto.
                  - Al ser datos inmutables que solo cambian cuando cambia
                    la lista products, useMemo evita el cómputo redundante
                    entre renders (ej. repintados por CSS, hovers).
                  Dependencias: products (única entrada mutable).
                */}
                <tbody>{useMemo(() => products.map((product) => {
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
                          <Link href={`/backoffice/inventory/orders/inbound?sku_id=${product.id}`} aria-label={`Registrar recepción para ${product.sku}`}>Registrar recepción</Link>
                          <Link href={`/backoffice/inventory/orders/outbound?sku_id=${product.id}`} aria-label={`Registrar despacho o pérdida para ${product.sku}`}>Registrar despacho o pérdida</Link>
                        </div>
                      </td>
                    </tr>
                  );
                }), [products])}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}
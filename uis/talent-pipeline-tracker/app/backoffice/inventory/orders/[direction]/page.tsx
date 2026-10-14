"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { createInboundOrder, createOutboundOrder, getProduct, type Product, type ExitType } from "@/lib/inventory";

export default function OrderPage() {
  const { direction } = useParams<{ direction: string }>();
  const inbound = direction === "inbound";
  const validDirection = inbound || direction === "outbound";
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [exitType, setExitType] = useState<ExitType>("dispatch");

  useEffect(() => {
    let active = true;
    const id = Number(new URLSearchParams(window.location.search).get("sku_id"));
    const load = async () => {
      if (!validDirection || !Number.isSafeInteger(id) || id <= 0) {
        throw new Error("Selecciona un producto y un tipo de orden válidos desde el inventario.");
      }
      return getProduct(id);
    };
    load()
      .then((data) => { if (active) setProduct(data); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "No se pudo cargar el producto.");
      });
    return () => { active = false; };
  }, [validDirection]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!product || saving) return;
    const data = new FormData(event.currentTarget);
    const quantity = Number(data.get("quantity"));
    if (!Number.isSafeInteger(quantity) || quantity <= 0) {
      setError("La cantidad debe ser un número entero mayor que cero.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const input = { sku_id: product.id, warehouse: product.warehouse, quantity };
      if (inbound) {
        const reference = String(data.get("reference") ?? "").trim();
        if (!reference) throw new Error("La referencia de recepción es obligatoria.");
        await createInboundOrder({ ...input, reference });
      } else {
        const tracking = String(data.get("tracking_number") ?? "").trim();
        if (exitType === "dispatch" && !tracking) throw new Error("El número de seguimiento es obligatorio para un despacho.");
        await createOutboundOrder({ ...input, exit_type: exitType, tracking_number: exitType === "loss" ? null : tracking });
      }
      setSuccess(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo registrar la orden.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="workspace">
      <Link className="back-link" href="/backoffice/inventory/products">Volver a productos</Link>
      <header className="page-heading"><div><p className="eyebrow">Inventario · TrackFlow</p><h1>{validDirection ? inbound ? "Crear entrada" : "Crear salida" : "Orden no válida"}</h1></div></header>
      {error && <p role="alert" className="action-error">{error}</p>}
      {!product && !error && <p role="status">Cargando producto...</p>}
      {success ? <p role="status" className="status status-selected">Orden registrada correctamente.</p> : product && (
        <form className="candidate-form max-w-2xl" onSubmit={submit}>
          <h2>{product.name}</h2>
          <p className="subtitle mb-6">{product.sku} · {product.client_name} · {product.warehouse} · Stock actual: {product.current_stock}</p>
          <fieldset disabled={saving} className="candidate-form-grid">
            <legend className="sr-only">Datos de la orden</legend>
            <label>Cantidad<input name="quantity" type="number" min={1} step={1} required /></label>
            {inbound ? <label>Referencia de recepción<input name="reference" required /></label> : (
              <>
                <label>Tipo de salida
                  <select aria-label="Tipo de salida" name="exit_type" value={exitType} onChange={(event) => setExitType(event.target.value as ExitType)} className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[var(--foreground)]">
                    <option value="dispatch">Despacho</option><option value="loss">Pérdida</option>
                  </select>
                </label>
                {exitType === "dispatch" && <label className="candidate-form-wide">Número de seguimiento<input name="tracking_number" required /></label>}
              </>
            )}
            <button type="submit" className="candidate-form-wide" disabled={saving}>{saving ? "Registrando..." : inbound ? "Registrar entrada" : "Registrar salida"}</button>
          </fieldset>
        </form>
      )}
    </main>
  );
}
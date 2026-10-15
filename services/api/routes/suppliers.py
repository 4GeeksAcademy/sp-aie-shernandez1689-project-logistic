from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_roles
from cache import (
    CACHE_KEY_SUPPLIERS_DETAIL,
    CACHE_KEY_SUPPLIERS_LIST,
    supplier_cache,
)
from database import get_suppliers_table
from models import (
    SupplierCreate,
    SupplierCreatedResponse,
    SupplierListResponse,
    SupplierRateUpdate,
    SupplierResponse,
    SupplierStatusUpdate,
)


router = APIRouter(prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(get_current_user)])
require_editor = Depends(require_roles("admin", "manager"))


def _invalidate_supplier_caches() -> None:
    """Invalida todas las claves de proveedores al escribir."""
    supplier_cache.invalidate_prefix(CACHE_KEY_SUPPLIERS_LIST)
    supplier_cache.invalidate_prefix(CACHE_KEY_SUPPLIERS_DETAIL)


def _invalidate_supplier_detail(supplier_id: int) -> None:
    """Invalida solo la clave de detalle de un proveedor específico."""
    supplier_cache.invalidate(f"{CACHE_KEY_SUPPLIERS_DETAIL}{supplier_id}")


def _build_supplier_list_cache_key(
    country: str | None,
    category: str | None,
) -> str:
    parts = [CACHE_KEY_SUPPLIERS_LIST]
    parts.append(f"country={country or ''}")
    parts.append(f"category={category or ''}")
    return "|".join(parts)


@router.post("", response_model=SupplierCreatedResponse, status_code=201, dependencies=[require_editor])
def create_supplier(payload: SupplierCreate) -> SupplierCreatedResponse:
    db, suppliers_table = get_suppliers_table()
    try:
        supplier_record = SupplierResponse(**payload.model_dump()).model_dump(mode="json")
        supplier_id = suppliers_table.insert(supplier_record)
        _invalidate_supplier_caches()
        return SupplierCreatedResponse(id=supplier_id, **supplier_record)
    finally:
        db.close()


@router.get("", response_model=list[SupplierListResponse])
def list_suppliers(
    country: Literal["USA", "Spain"] | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[SupplierListResponse]:
    """
    Cachea listados hasta 120 s porque:
    - Coste: O(n) con filtrado en listas y re-hidratación Pydantic.
    - Frecuencia: media-alta (gestión de inventario).
    - Estabilidad: los proveedores cambian poco (solo ediciones
      manuales de tarifa/estado).
    - Los datos son globales/compartidos.
    """
    cache_key = _build_supplier_list_cache_key(country, category)
    hit, cached_result = supplier_cache.get(cache_key)
    if hit:
        return cached_result

    db, suppliers_table = get_suppliers_table()
    try:
        documents = suppliers_table.all()

        filtered_documents = documents
        if country is not None:
            filtered_documents = [doc for doc in filtered_documents if doc.get("country") == country]
        if category is not None:
            filtered_documents = [
                doc
                for doc in filtered_documents
                if category in (doc.get("categories") or [])
            ]

        result = [
            SupplierListResponse.model_validate({**dict(doc), "id": doc.doc_id})
            for doc in filtered_documents
        ]
        supplier_cache.set(cache_key, result)
        return result
    finally:
        db.close()


@router.get("/{supplier_id}", response_model=SupplierCreatedResponse)
def get_supplier_by_id(supplier_id: int) -> SupplierCreatedResponse:
    """
    Cachea detalle de proveedor hasta 120 s porque:
    - Coste: bajo (O(1) lookup por doc_id), pero se llama en cada
      vista de detalle y las escrituras son poco frecuentes.
    - Se invalida la clave específica al editar ese proveedor.
    """
    cache_key = f"{CACHE_KEY_SUPPLIERS_DETAIL}{supplier_id}"
    hit, cached_result = supplier_cache.get(cache_key)
    if hit:
        return cached_result

    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        result = SupplierCreatedResponse(id=document.doc_id, **dict(document))
        supplier_cache.set(cache_key, result)
        return result
    finally:
        db.close()


@router.patch("/{supplier_id}/rate", response_model=SupplierCreatedResponse, dependencies=[require_editor])
def update_supplier_rate(
    supplier_id: int,
    payload: SupplierRateUpdate,
) -> SupplierCreatedResponse:
    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        updated_at = datetime.now(timezone.utc).isoformat()
        suppliers_table.update(
            {
                "rate_per_shipment": payload.rate_per_shipment,
                "updated_at": updated_at,
            },
            doc_ids=[supplier_id],
        )

        _invalidate_supplier_caches()
        _invalidate_supplier_detail(supplier_id)

        updated_document = suppliers_table.get(doc_id=supplier_id)
        return SupplierCreatedResponse(id=updated_document.doc_id, **dict(updated_document))
    finally:
        db.close()


@router.patch("/{supplier_id}/status", response_model=SupplierCreatedResponse, dependencies=[require_editor])
def update_supplier_status(
    supplier_id: int,
    payload: SupplierStatusUpdate,
) -> SupplierCreatedResponse:
    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        suppliers_table.update({"status": payload.status.value}, doc_ids=[supplier_id])

        _invalidate_supplier_caches()
        _invalidate_supplier_detail(supplier_id)

        updated_document = suppliers_table.get(doc_id=supplier_id)
        return SupplierCreatedResponse(id=updated_document.doc_id, **dict(updated_document))
    finally:
        db.close()


@router.delete("/{supplier_id}", response_model=None, status_code=204, dependencies=[require_editor])
def delete_supplier(supplier_id: int) -> None:
    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        suppliers_table.remove(doc_ids=[supplier_id])
        _invalidate_supplier_caches()
        _invalidate_supplier_detail(supplier_id)
        return None
    finally:
        db.close()
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from database import get_suppliers_table
from models import (
    SupplierCreate,
    SupplierCreatedResponse,
    SupplierRateUpdate,
    SupplierResponse,
    SupplierStatusUpdate,
)


router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.post("", response_model=SupplierCreatedResponse, status_code=201)
def create_supplier(payload: SupplierCreate) -> SupplierCreatedResponse:
    db, suppliers_table = get_suppliers_table()
    try:
        supplier_record = SupplierResponse(**payload.model_dump()).model_dump(mode="json")
        supplier_id = suppliers_table.insert(supplier_record)
        return SupplierCreatedResponse(id=supplier_id, **supplier_record)
    finally:
        db.close()


@router.get("", response_model=list[SupplierCreatedResponse])
def list_suppliers(
    country: Literal["USA", "Spain"] | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[SupplierCreatedResponse]:
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

        return [SupplierCreatedResponse(id=doc.doc_id, **dict(doc)) for doc in filtered_documents]
    finally:
        db.close()


@router.get("/{supplier_id}", response_model=SupplierCreatedResponse)
def get_supplier_by_id(supplier_id: int) -> SupplierCreatedResponse:
    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        return SupplierCreatedResponse(id=document.doc_id, **dict(document))
    finally:
        db.close()


@router.patch("/{supplier_id}/rate", response_model=SupplierCreatedResponse)
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

        updated_document = suppliers_table.get(doc_id=supplier_id)
        return SupplierCreatedResponse(id=updated_document.doc_id, **dict(updated_document))
    finally:
        db.close()


@router.patch("/{supplier_id}/status", response_model=SupplierCreatedResponse)
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

        updated_document = suppliers_table.get(doc_id=supplier_id)
        return SupplierCreatedResponse(id=updated_document.doc_id, **dict(updated_document))
    finally:
        db.close()


@router.delete("/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: int) -> None:
    db, suppliers_table = get_suppliers_table()
    try:
        document = suppliers_table.get(doc_id=supplier_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Supplier not found")

        suppliers_table.remove(doc_ids=[supplier_id])
        return None
    finally:
        db.close()
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db import DBSession
from app.models import AuthUser, SKU, StockEntry, StockExit
from app.schemas import (
    SKUCreateRequest,
    SKUResponse,
    StockEntryCreateRequest,
    StockEntryOrderResponse,
    StockEntryResponse,
    StockExitCreateRequest,
    StockExitOrderResponse,
    StockExitResponse,
)


router = APIRouter(
    prefix="/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)]
)


def _get_sku(session: Session, sku_id: int, lock: bool = False) -> SKU:
    statement = select(SKU).where(SKU.id == sku_id)
    if lock:
        statement = statement.with_for_update()
    product = session.exec(statement).first()
    if product is None:
        raise HTTPException(status_code=404, detail="SKU not found")
    return product


def _current_stock(session: Session, sku_id: int, warehouse: str) -> int:
    entries = select(func.coalesce(func.sum(StockEntry.quantity), 0)).where(
        StockEntry.sku_id == sku_id, StockEntry.warehouse == warehouse
    ).scalar_subquery()
    exits = select(func.coalesce(func.sum(StockExit.quantity), 0)).where(
        StockExit.sku_id == sku_id, StockExit.warehouse == warehouse
    ).scalar_subquery()
    return session.exec(select(entries - exits)).one()


def _product_response(session: Session, product: SKU) -> SKUResponse:
    return SKUResponse.model_validate({
        **product.model_dump(),
        "current_stock": _current_stock(session, product.id, product.warehouse),
    })


def _check_warehouse(product: SKU, warehouse: str) -> None:
    if warehouse != product.warehouse:
        raise HTTPException(status_code=400, detail="Warehouse must match the SKU warehouse")


@router.get("/products", response_model=list[SKUResponse])
def list_products(session: DBSession) -> list[SKUResponse]:
    products = session.exec(select(SKU).order_by(SKU.id)).all()
    return [_product_response(session, product) for product in products]


@router.post("/products", response_model=SKUResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: SKUCreateRequest, session: DBSession) -> SKUResponse:
    product = SKU(**payload.model_dump())
    session.add(product)
    session.commit()
    session.refresh(product)
    return _product_response(session, product)


@router.get("/products/{id}", response_model=SKUResponse)
def get_product(id: int, session: DBSession) -> SKUResponse:
    return _product_response(session, _get_sku(session, id))


@router.post("/orders/inbound", response_model=StockEntryResponse, status_code=status.HTTP_201_CREATED)
def create_inbound_order(
    payload: StockEntryCreateRequest,
    session: DBSession,
    current_user: AuthUser = Depends(get_current_user),
) -> StockEntryResponse:
    product = _get_sku(session, payload.sku_id, lock=True)
    _check_warehouse(product, payload.warehouse)
    entry = StockEntry(**payload.model_dump(), user_uuid=current_user.id)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return StockEntryResponse.model_validate(entry)


@router.post("/orders/outbound", response_model=StockExitResponse, status_code=status.HTTP_201_CREATED)
def create_outbound_order(
    payload: StockExitCreateRequest,
    session: DBSession,
    current_user: AuthUser = Depends(get_current_user),
) -> StockExitResponse:
    product = _get_sku(session, payload.sku_id, lock=True)
    _check_warehouse(product, payload.warehouse)
    available = _current_stock(session, payload.sku_id, payload.warehouse)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock for SKU '{product.sku}'. Available: {available}, requested: {payload.quantity}.",
        )
    exit_order = StockExit(**payload.model_dump(), user_uuid=current_user.id)
    session.add(exit_order)
    session.commit()
    session.refresh(exit_order)
    return StockExitResponse.model_validate(exit_order)


@router.get("/orders", response_model=list[StockEntryOrderResponse | StockExitOrderResponse])
def list_orders(session: DBSession) -> list[StockEntryOrderResponse | StockExitOrderResponse]:
    products = {
        product.id: _product_response(session, product)
        for product in session.exec(select(SKU)).all()
    }
    orders = []
    for entry in session.exec(select(StockEntry)).all():
        orders.append(StockEntryOrderResponse(
            **entry.model_dump(), product=products[entry.sku_id]
        ))
    for exit_order in session.exec(select(StockExit)).all():
        orders.append(StockExitOrderResponse(
            **exit_order.model_dump(), product=products[exit_order.sku_id]
        ))
    return sorted(orders, key=lambda order: (order.created_at, order.order_type, order.id))
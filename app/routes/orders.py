from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..db import SessionLocal
from ..models.order import Order, OrderItem
from ..schemas.order import OrderStatusUpdate, OrderPaymentStatusUpdate
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id, require_restaurant_access

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET live orders
@router.get("/api/v1/orders/live", response_model=list[dict])
def get_live_orders(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    ).filter(Order.status != "SERVED")
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    orders = query.all()
    response = []
    for o in orders:
        items = [
            {
                "name": i.menu_item.name if i.menu_item else "Unknown Item",
                "quantity": i.quantity,
                "price": i.price
            }
            for i in o.items
        ]

        # Use defaults if not set
        method = o.payment_method or "Cash"
        # If SERVED, it's typically paid. If PENDING, maybe pending.
        p_status = o.payment_status or ("Paid" if o.status in ["SERVED", "COMPLETED"] else "Pending")

        response.append({
            "order_id": o.id,
            "table_number": "Takeaway" if str(o.table_id).lower() == "takeaway" else (o.table.table_number if o.table else "N/A"),
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items
        })

    return response

# PATCH order status
@router.patch("/api/v1/orders/{order_id}/status", response_model=dict)
def update_status(
    order_id: int,
    data: OrderStatusUpdate,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    require_restaurant_access(user, order.restaurant_id)
    order.status = data.status
    db.commit()

    return {
        "order_id": order.id,
        "status": order.status
    }

# PATCH order payment status
@router.patch("/api/v1/orders/{order_id}/payment-status", response_model=dict)
def update_payment_status(
    order_id: int,
    data: OrderPaymentStatusUpdate,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    require_restaurant_access(user, order.restaurant_id)
    order.payment_status = data.payment_status
    db.commit()

    return {
        "order_id": order.id,
        "payment_status": order.payment_status
    }


# GET all orders (for payments and history)
@router.get("/api/v1/orders", response_model=list[dict])
def get_all_orders(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    )
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    # For payment dashboard, ordering by latest first
    orders = query.order_by(Order.created_at.desc()).all()
    
    response = []
    for o in orders:
        items = [
            {
                "name": i.menu_item.name if i.menu_item else "Unknown Item",
                "quantity": i.quantity,
                "price": i.price
            }
            for i in o.items
        ]

        # Use defaults if not set
        method = o.payment_method or "Cash"
        # If SERVED, it's typically paid. If PENDING, maybe pending.
        p_status = o.payment_status or ("Paid" if o.status in ["SERVED", "COMPLETED"] else "Pending")

        response.append({
            "order_id": o.id,
            "table_number": "Takeaway" if str(o.table_id).lower() == "takeaway" else (o.table.table_number if o.table else "N/A"),
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items
        })

    return response
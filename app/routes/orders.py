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

# GET order status (public)
@router.get("/api/v1/orders/{order_id}/status", response_model=dict)
def get_order_status(
    order_id: str,
    db: Session = Depends(get_db)
):
    try:
        cleaned_id = order_id.replace("ORD-", "").replace("UDP-", "")
        order_id_int = int(cleaned_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Order not found")

    order = db.query(Order).filter(Order.id == order_id_int).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "order_id": order.id,
        "status": order.status,
        "payment_status": order.payment_status
    }

# GET active order by table number (public)
@router.get("/api/v1/public/orders/table/{table_number}", response_model=dict)
def get_active_order_by_table(
    table_number: str,
    db: Session = Depends(get_db)
):
    from ..models.table import Table
    table = db.query(Table).filter(Table.table_number == table_number).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
        
    order = db.query(Order).filter(
        Order.table_id == table.id,
        Order.status.notin_(["COMPLETED", "CANCELLED", "SERVED"])
    ).order_by(Order.created_at.desc()).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="No active order for this table")
        
    return {
        "order_id": order.id,
        "status": order.status,
        "payment_status": order.payment_status
    }

from pydantic import BaseModel
from typing import List, Optional

class PublicOrderItem(BaseModel):
    id: int
    quantity: int
    price: float
    note: Optional[str] = ""

class PublicOrderCreate(BaseModel):
    table_number: Optional[str] = None
    table_id: Optional[int] = None
    restaurant_id: Optional[int] = None
    payment_method: Optional[str] = "Cash"
    total_amount: float
    cart: List[PublicOrderItem]

@router.post("/api/v1/orders", response_model=dict)
def create_public_order(
    data: PublicOrderCreate,
    db: Session = Depends(get_db)
):
    mapped_table_id = None
    if data.table_number and data.table_number.lower() != 'takeaway':
        from ..models.table import Table
        t = db.query(Table).filter(Table.table_number == data.table_number).first()
        if t:
            mapped_table_id = t.id
    if data.table_id:
        mapped_table_id = data.table_id

    # Create the order
    order = Order(
        table_id=mapped_table_id,
        restaurant_id=data.restaurant_id or 1,
        payment_method=data.payment_method,
        total_amount=data.total_amount,
        status="PENDING",
        payment_status="Pending" if data.payment_method == "Cash" else "Paid"
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    
    # Add items
    for item in data.cart:
        order_item = OrderItem(
            order_id=order.id,
            menu_item_id=item.id,
            quantity=item.quantity,
            price=item.price
        )
        db.add(order_item)
    db.commit()
    
    return {
        "order_id": order.id,
        "status": order.status,
        "message": "Order placed successfully"
    }


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
            "table_number": o.table.table_number if o.table else "Takeaway",
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
            "table_number": o.table.table_number if o.table else "Takeaway",
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items
        })

    return response
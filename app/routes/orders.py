from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.order import Order, OrderItem
from ..schemas.order import OrderStatusUpdate
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id, require_restaurant_access

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Public POST order
from pydantic import BaseModel
from typing import List, Optional

class PublicCartItem(BaseModel):
    id: int
    quantity: int
    price: float
    note: Optional[str] = ""

class PublicOrderCreate(BaseModel):
    table_number: str
    payment_method: str
    phone: str
    cart: List[PublicCartItem]
    subtotal: float
    gst: float
    service_charge: float
    total_amount: float

@router.post("/api/v1/orders", response_model=dict)
def create_public_order(
    data: PublicOrderCreate,
    db: Session = Depends(get_db)
):
    from ..models.table import Table
    from ..models.menu import MenuItem
    from ..models.restaurant import Restaurant

    # Try to find table or use a default one
    table = db.query(Table).filter(Table.table_number == data.table_number).first()
    if not table:
        # Try matching with T- prefix if not present, e.g. T-06 instead of 06
        alt_table_number = f"T-{data.table_number}"
        table = db.query(Table).filter(Table.table_number == alt_table_number).first()
    if not table:
        # Try finding table containing the number
        table = db.query(Table).filter(Table.table_number.like(f"%{data.table_number}%")).first()

    table_id = table.id if table else None

    # Resolve restaurant_id
    restaurant_id = None
    if table and table.restaurant_id:
        restaurant_id = table.restaurant_id
    
    if restaurant_id is None and data.cart:
        menu_item = db.query(MenuItem).filter(MenuItem.id == data.cart[0].id).first()
        if menu_item and menu_item.restaurant_id:
            restaurant_id = menu_item.restaurant_id
            
    if restaurant_id is None:
        first_restaurant = db.query(Restaurant).first()
        if first_restaurant:
            restaurant_id = first_restaurant.id

    # Determine status based on payment method
    status = "CONFIRMED" if data.payment_method in ["Razorpay", "UPI"] else "PENDING"
    payment_status = "Paid" if data.payment_method in ["Razorpay", "UPI"] else "Pending"

    order = Order(
        table_id=table_id,
        restaurant_id=restaurant_id,
        status=status,
        payment_method=data.payment_method,
        payment_status=payment_status,
        total_amount=data.total_amount
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    
    for item in data.cart:
        order_item = OrderItem(
            order_id=order.id,
            menu_item_id=item.id,
            quantity=item.quantity,
            price=item.price
        )
        db.add(order_item)
    db.commit()
    
    return {"id": order.id, "status": order.status}



# GET live orders
@router.get("/api/v1/orders/live", response_model=list[dict])
def get_live_orders(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(Order).filter(Order.status != "SERVED")
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    orders = query.all()
    response = []
    for o in orders:
        items = [
            {
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "price": i.price
            }
            for i in o.items
        ]

        response.append({
            "order_id": o.id,
            "table_number": o.table.table_number if o.table else "TakeAway",
            "status": o.status,
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


# GET all orders (for payments and history)
@router.get("/api/v1/orders", response_model=list[dict])
def get_all_orders(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(Order)
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    # For payment dashboard, ordering by latest first
    orders = query.order_by(Order.created_at.desc()).all()
    
    response = []
    for o in orders:
        items = [
            {
                "name": i.menu_item.name,
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
            "table_number": o.table.table_number if o.table else "TakeAway",
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items
        })

    return response
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.menu import MenuCategory, MenuItem
from ..models.restaurant import Restaurant
from ..models.order import Order, OrderItem
from ..models.table import Table
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import hmac
import hashlib
import time
from ..utils.table_refs import build_table_number_map, resolve_order_table_number

try:
    import razorpay
except Exception:
    razorpay = None

router = APIRouter(tags=["Customer"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Models for Request Bodies
class CustomerCartItem(BaseModel):
    id: int
    quantity: int
    price: float

class CustomerOrderPayload(BaseModel):
    table_number: str = "06"
    payment_method: str = "UPI"
    phone: str = ""
    cart: List[CustomerCartItem] = []
    subtotal: float = 0
    gst: float = 0
    service_charge: float = 0
    total_amount: float = 0
    
class RazorpayOrderPayload(BaseModel):
    amount: float
    currency: str = "INR"
    receipt: Optional[str] = None

class RazorpayVerifyPayload(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

@router.get("/api/categories")
def get_categories(restaurant_id: int = 1, db: Session = Depends(get_db)):
    categories = db.query(MenuCategory).filter(MenuCategory.restaurant_id == restaurant_id).order_by(MenuCategory.id.asc()).all()
    return categories

@router.get("/api/items")
def get_items(restaurant_id: int = 1, db: Session = Depends(get_db)):
    items = db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id, MenuItem.is_available == True).order_by(MenuItem.category_id.asc(), MenuItem.id.asc()).all()
    return items

@router.get("/api/items/category/{category_id}")
def get_items_by_category(category_id: int, restaurant_id: int = 1, db: Session = Depends(get_db)):
    items = db.query(MenuItem).filter(
        MenuItem.restaurant_id == restaurant_id, 
        MenuItem.category_id == category_id,
        MenuItem.is_available == True
    ).order_by(MenuItem.id.asc()).all()
    return items

@router.get("/api/restaurant")
def get_restaurant(restaurant_id: int = 1, db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        return {}
    return restaurant

@router.post("/api/orders")
def place_order(payload: CustomerOrderPayload, restaurant_id: int = 1, db: Session = Depends(get_db)):
    try:
        # Find or default table_id
        is_takeaway = (
            not payload.table_number or 
            payload.table_number.lower().replace(" ", "").replace("-", "") == "takeaway"
        )
        if is_takeaway:
            table_id = None
        else:
            table = db.query(Table).filter(Table.table_number == payload.table_number, Table.restaurant_id == restaurant_id).first()
            if not table:
                table = Table(table_number=payload.table_number, restaurant_id=restaurant_id, capacity=4, status="Occupied")
                db.add(table)
                db.commit()
                db.refresh(table)
            table_id = table.id

        status = "CONFIRMED" if payload.payment_method in ["Razorpay", "UPI"] else "PENDING"
        payment_status = "Paid" if payload.payment_method in ["Razorpay", "UPI"] else "Pending"

        new_order = Order(
            restaurant_id=restaurant_id,
            table_id=table_id,
            total_amount=payload.total_amount,
            status=status,
            payment_status=payment_status,
            payment_method=payload.payment_method
        )
        db.add(new_order)
        db.commit()
        db.refresh(new_order)

        for item in payload.cart:
            order_item = OrderItem(
                order_id=new_order.id,
                menu_item_id=item.id,
                quantity=item.quantity,
                price=item.price
            )
            db.add(order_item)
        
        db.commit()

        return {
            "success": True,
            "orderId": f"ORD-{str(new_order.id).zfill(6)}",
            "dbOrderId": new_order.id,
            "message": "Order placed successfully"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/create-razorpay-order")
def create_razorpay_order(payload: RazorpayOrderPayload):
    try:
        if razorpay is None:
            raise HTTPException(status_code=503, detail="Razorpay integration is unavailable on this server")

        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise HTTPException(status_code=500, detail="Razorpay credentials not configured")

        client = razorpay.Client(auth=(key_id, key_secret))
        
        receipt = payload.receipt or f"rcpt_{int(time.time()*1000)}"
        
        data = {
            "amount": int(payload.amount * 100),
            "currency": payload.currency,
            "receipt": receipt
        }
        
        order = client.order.create(data=data)
        return {"success": True, "order": order}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/verify-payment")
def verify_payment(payload: RazorpayVerifyPayload):
    try:
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not key_secret:
            raise HTTPException(status_code=500, detail="Razorpay credentials not configured")
            
        sign = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}"
        expected_sign = hmac.new(
            key_secret.encode(),
            sign.encode(),
            hashlib.sha256
        ).hexdigest()

        if payload.razorpay_signature == expected_sign:
            return {"success": True, "message": "Payment verified successfully"}
        else:
            raise HTTPException(status_code=400, detail="Invalid signature sent!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/orders/{order_id}")
def get_order_by_id(order_id: int, restaurant_id: int = 1, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id, Order.restaurant_id == restaurant_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    table_number_map = build_table_number_map(db, [order])

    items = []
    for oi in order.items:
        items.append({
            "id": oi.id,
            "order_id": oi.order_id,
            "menu_item_id": oi.menu_item_id,
            "quantity": oi.quantity,
            "price": oi.price,
            "name": oi.menu_item.name if oi.menu_item else None,
            "description": oi.menu_item.description if oi.menu_item else None,
            "image_url": oi.menu_item.image_url if oi.menu_item else None
        })
        
    order_dict = {
        "id": order.id,
        "orderId": f"ORD-{str(order.id).zfill(6)}",
        "restaurant_id": order.restaurant_id,
        "table_id": order.table_id,
        "table_number": resolve_order_table_number(order, table_number_map),
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "total_amount": order.total_amount,
        "created_at": order.created_at,
        "updated_at": order.updated_at
    }

    return {"order": order_dict, "items": items}

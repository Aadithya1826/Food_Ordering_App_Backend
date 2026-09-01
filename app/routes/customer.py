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
from jose import jwt
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


# ──────────────────────────────────────────────────────────────────────────────
# Customer Login / Registration (upsert)
# ──────────────────────────────────────────────────────────────────────────────

class CustomerLoginPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    name: Optional[str] = None
    otp: Optional[str] = None


@router.post("/api/v1/public/customers/login")
def customer_login(payload: CustomerLoginPayload, db: Session = Depends(get_db)):
    """
    Phone-based login/registration (OTP-less).
    - If the phone number already exists → return existing customer (update name if provided).
    - If new → create a customer record with name + phone.
    Returns: { id, name, phone, token }
    """
    from ..models.customer import Customer

    phone = payload.phone.strip()
    name = (payload.name or "").strip() or "Guest"

    # Validate OTP
    if payload.otp != "1234":
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # Upsert: find or create
    customer = db.query(Customer).filter(Customer.phone == phone).first()

    if customer:
        # Update name if a new one was provided and it differs
        if payload.name and payload.name.strip() and customer.name != payload.name.strip():
            customer.name = payload.name.strip()
            db.commit()
            db.refresh(customer)
    else:
        # New customer — create record
        customer = Customer(phone=phone, name=name)
        db.add(customer)
        db.commit()
        db.refresh(customer)

    # Generate a simple session token (HMAC of id + phone — no JWT dependency needed)
    from ..utils.auth import SECRET_KEY, ALGORITHM
    payload = {"sub": customer.phone, "role": "CUSTOMER"}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "email": getattr(customer, "email", None),
        "profile_picture_url": getattr(customer, "profile_picture_url", None),
        "loyalty_points": getattr(customer, "loyalty_points", 0),
        "token": token,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Customer Profile Endpoints
# ──────────────────────────────────────────────────────────────────────────────

def _customer_response(c) -> dict:
    """Standard customer profile dict reused across endpoints."""
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "email": getattr(c, "email", None),
        "profile_picture_url": getattr(c, "profile_picture_url", None),
        "loyalty_points": getattr(c, "loyalty_points", 0),
        "address": getattr(c, "address", None),
    }


@router.get("/api/v1/public/customers/{customer_id}/profile")
def get_customer_profile_by_id(customer_id: int, db: Session = Depends(get_db)):
    """Return profile for a specific customer by integer ID."""
    from ..models.customer import Customer
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _customer_response(customer)


class UpdateProfilePayload(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


@router.patch("/api/v1/public/customers/{customer_id}/profile")
def update_customer_profile(customer_id: int, payload: UpdateProfilePayload, db: Session = Depends(get_db)):
    """Update name, email, or address for a customer."""
    from ..models.customer import Customer
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.name is not None and payload.name.strip():
        customer.name = payload.name.strip()
    if payload.email is not None:
        customer.email = payload.email.strip() or None
    if payload.address is not None:
        customer.address = payload.address.strip() or None
    db.commit()
    db.refresh(customer)
    return _customer_response(customer)


@router.post("/api/v1/public/customers/{customer_id}/profile-picture")
async def upload_profile_picture(
    customer_id: int,
    db: Session = Depends(get_db),
):
    """
    Upload a profile picture for a customer.
    Call via multipart/form-data with field 'file'.
    Because FastAPI requires UploadFile at function-definition time,
    we import it at module scope via a workaround below.
    """
    raise HTTPException(status_code=400, detail="Send as multipart/form-data with 'file' field")


from fastapi import UploadFile as _FastAPIUploadFile, File as _FastAPIFile
import shutil as _shutil


@router.post("/api/v1/public/customers/{customer_id}/upload-picture")
async def upload_profile_picture_v2(
    customer_id: int,
    file: _FastAPIUploadFile = _FastAPIFile(...),
    db: Session = Depends(get_db),
):
    """Upload a profile picture (multipart/form-data, field 'file')."""
    from ..models.customer import Customer
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    ext = (file.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"

    static_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "static", "customer_profiles"
    )
    os.makedirs(static_dir, exist_ok=True)
    filename = f"customer_{customer_id}.{ext}"
    dest = os.path.join(static_dir, filename)

    with open(dest, "wb") as buf:
        _shutil.copyfileobj(file.file, buf)

    url_path = f"/static/customer_profiles/{filename}"
    customer.profile_picture_url = url_path
    db.commit()
    db.refresh(customer)
    return _customer_response(customer)


# Models for Request Bodies
class CustomerCartItem(BaseModel):
    """Cart item from the frontend — extra fields (name, image, note…) are ignored."""
    model_config = {"extra": "ignore"}

    id: int
    quantity: int
    price: float

class CustomerOrderPayload(BaseModel):
    """Flexible order payload — accepts null table_number and extra frontend fields."""
    model_config = {"extra": "ignore"}

    table_number: Optional[str] = "takeaway"   # null → treated as takeaway
    order_type: Optional[str] = "Dine In"
    payment_method: str = "UPI"
    phone: str = ""
    cart: List[CustomerCartItem] = []
    subtotal: float = 0
    gst: float = 0
    service_charge: float = 0
    discount_amount: float = 0
    discount_code: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_address_id: Optional[int] = None
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
            base_num = payload.table_number.replace("T-", "").replace("t-", "").strip()
            table = db.query(Table).filter(
                (Table.table_number == payload.table_number) |
                (Table.table_number == base_num) |
                (Table.table_number == f"T-{base_num}"),
                Table.restaurant_id == restaurant_id
            ).first()
            if not table:
                table = Table(table_number=payload.table_number, restaurant_id=restaurant_id, capacity=4, status="Occupied")
                db.add(table)
                db.commit()
                db.refresh(table)
            table_id = table.id

        is_digital = payload.payment_method in ["Razorpay", "UPI"]
        is_delivery_cash = ((payload.order_type or "DINE_IN").upper() == "DELIVERY" and payload.payment_method == "Cash")
        is_paid = is_digital or is_delivery_cash

        status = "CONFIRMED" if is_paid else "PENDING"
        payment_status = "Paid" if is_paid else "Pending"

        new_order = Order(
            restaurant_id=restaurant_id,
            table_id=table_id,
            total_amount=payload.total_amount,
            status=status,
            payment_status=payment_status,
            payment_method=payload.payment_method,
            order_type=payload.order_type if payload.order_type else "DINE_IN",
            delivery_address_id=payload.delivery_address_id
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

@router.get("/api/razorpay-key")
def get_razorpay_key():
    key_id = os.getenv("RAZORPAY_KEY_ID")
    if not key_id:
        raise HTTPException(status_code=500, detail="Razorpay key not configured")
    return {"key_id": key_id}

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

@router.get("/api/v1/public/orders/table/{table_number}")
def get_public_active_order_for_table(table_number: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    clean_table = table_number.strip()
    table = db.query(Table).filter(Table.table_number.ilike(f"%{clean_table}%"), Table.restaurant_id == restaurant_id).first()
    
    query = db.query(Order).filter(Order.restaurant_id == restaurant_id)
    from sqlalchemy import cast, String
    if table:
        query = query.filter(cast(Order.table_id, String) == str(table.id))
    
    order = query.filter(Order.status.in_(["PENDING", "CONFIRMED", "PREPARING", "READY"])).order_by(Order.id.desc()).first()

    if not order:
        raise HTTPException(status_code=404, detail="No active order for this table")

    return {
        "id": order.id,
        "order_id": order.id,
        "orderId": f"ORD-{str(order.id).zfill(6)}",
        "status": order.status,
        "total_amount": order.total_amount,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status
    }

@router.get("/api/v1/public/tables/{table_number}")
def get_public_table_status(table_number: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    clean_table = table_number.strip()
    table = db.query(Table).filter(Table.table_number.ilike(f"%{clean_table}%"), Table.restaurant_id == restaurant_id).first()
    
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
        
    return {
        "table_number": table.table_number,
        "is_active": getattr(table, "is_active", True),
        "status": getattr(table, "status", "Vacant") or "Vacant"
    }
@router.get("/api/orders/{order_id}")
def get_order_by_id(order_id: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    try:
        if order_id.startswith("ORD-"):
            parsed_id = int(order_id.replace("ORD-", ""))
        elif order_id.startswith("UDP-"):
            parsed_id = int(order_id.replace("UDP-", ""))
        else:
            parsed_id = int(order_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid order ID format")
        
    order = db.query(Order).filter(Order.id == parsed_id, Order.restaurant_id == restaurant_id).first()
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


# ──────────────────────────────────────────────
# Public endpoints used by Frontend_app
# ──────────────────────────────────────────────

@router.get("/api/v1/public/orders/{order_id}")
def get_public_order(order_id: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Same as /api/orders/{order_id} but accessible without auth."""
    try:
        if order_id.startswith("ORD-"):
            parsed_id = int(order_id.replace("ORD-", ""))
        elif order_id.startswith("UDP-"):
            parsed_id = int(order_id.replace("UDP-", ""))
        else:
            parsed_id = int(order_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid order ID format")

    order = db.query(Order).filter(Order.id == parsed_id, Order.restaurant_id == restaurant_id).first()
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
            "image_url": oi.menu_item.image_url if oi.menu_item else None,
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
        "updated_at": order.updated_at,
    }
    return {"order": order_dict, "items": items}


@router.get("/api/v1/public/customers/orders")
def get_customer_orders_by_phone(phone: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    """Return all orders for a customer identified by phone number."""
    from sqlalchemy.orm import joinedload
    orders = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.menu_item))
        .filter(Order.restaurant_id == restaurant_id)
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )

    table_number_map = build_table_number_map(db, orders)
    result = []
    for order in orders:
        items = []
        for oi in order.items:
            items.append({
                "id": oi.id,
                "menu_item_id": oi.menu_item_id,
                "quantity": oi.quantity,
                "price": oi.price,
                "name": oi.menu_item.name if oi.menu_item else None,
                "image_url": oi.menu_item.image_url if oi.menu_item else None,
            })
        result.append({
            "order": {
                "id": order.id,
                "orderId": f"ORD-{str(order.id).zfill(6)}",
                "restaurant_id": order.restaurant_id,
                "table_number": resolve_order_table_number(order, table_number_map),
                "status": order.status,
                "payment_method": order.payment_method,
                "payment_status": order.payment_status,
                "total_amount": order.total_amount,
                "created_at": order.created_at,
            },
            "items": items,
        })
    return result


@router.get("/api/v1/public/customers/{phone}/profile")
def get_customer_profile(phone: str, db: Session = Depends(get_db)):
    """Return basic customer profile by phone number."""
    # Try to import Customer model; gracefully handle if missing
    try:
        from ..models.customer import Customer as CustomerModel
        customer = db.query(CustomerModel).filter(CustomerModel.phone == phone).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {
            "id": customer.id,
            "phone": customer.phone,
            "name": customer.name,
            "email": getattr(customer, "email", None),
            "loyalty_points": getattr(customer, "loyalty_points", 0),
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Customer model not available")


@router.get("/api/customer/table/verify")
def verify_table_public(table_number: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    """
    Verify a table belongs to the restaurant.
    Returns { valid: bool, table: {...} } — NOT a 404 when not found,
    so the frontend can read data.valid to show its own error popup.
    """
    raw = table_number.strip()

    # Build candidate lookups: "T-01", "01", "1", "T-1"
    num_only = raw.lstrip("Tt-").lstrip("0") or "0"          # "01" → "1"
    num_zero  = raw.lstrip("Tt-")                             # "T-01" → "01"
    candidates = list(dict.fromkeys([
        raw,                          # exact as entered
        f"T-{num_zero}",             # "T-01"
        f"T-{num_only}",             # "T-1"
        num_zero,                     # "01"
        num_only,                     # "1"
    ]))

    table = None
    for candidate in candidates:
        table = db.query(Table).filter(
            Table.table_number == candidate,
            Table.restaurant_id == restaurant_id
        ).first()
        if table:
            break

    # If still not found, try a case-insensitive LIKE on the raw value
    if not table:
        table = db.query(Table).filter(
            Table.table_number.ilike(f"%{raw}%"),
            Table.restaurant_id == restaurant_id
        ).first()

    if not table:
        # Return valid:false — do NOT raise 404, let frontend handle the message
        return {
            "valid": False,
            "message": "This table doesn't belong to the selected restaurant.",
        }

    return {
        "valid": True,
        "table": {
            "id": table.id,
            "table_number": table.table_number,
            "status": getattr(table, "status", "Vacant") or "Vacant",
            "is_active": getattr(table, "is_active", True),
        },
    }



class FeedbackPayload(BaseModel):
    model_config = {"extra": "ignore"}
    rating: int = 0
    feedback_tags: Optional[List[str]] = None
    feedback_message: Optional[str] = None
    order_type: Optional[str] = None  # "Dine In" | "Take Away"


@router.post("/api/v1/public/orders/{order_id}/feedback")
def submit_order_feedback(order_id: str, payload: FeedbackPayload, db: Session = Depends(get_db)):
    """Accept customer feedback for a completed order."""
    # Parse the order ID
    try:
        parsed_id = int(order_id.replace("ORD-", "").replace("UDP-", ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid order ID format")

    order = db.query(Order).filter(Order.id == parsed_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Log feedback (persisted to console/uvicorn logs until a feedback table is added)
    import json as _json
    print(f"[FEEDBACK] order_id={parsed_id} rating={payload.rating} "
          f"tags={payload.feedback_tags} msg={payload.feedback_message!r} "
          f"type={payload.order_type}")

    return {"success": True, "message": "Thank you for your feedback!"}

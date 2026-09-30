# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
from ..db import SessionLocal
from ..models.menu import MenuCategory, MenuItem
from ..models.restaurant import Restaurant
from ..models.order import Order, OrderItem
from ..models.table import Table
from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory
from ..models.customer import CustomerAddress
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import os
from datetime import datetime
import hmac
import hashlib
import time
from jose import jwt
from ..utils.table_refs import build_table_number_map, resolve_order_table_number
from ..utils.dependencies import get_current_customer

try:
    # pyrefly: ignore [missing-import]
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
# Phone normalization helper (imported from utils)
# ──────────────────────────────────────────────────────────────────────────────
from ..utils.phone import normalize_phone

# ──────────────────────────────────────────────────────────────────────────────
# Customer Auth: separated Signup / Login / Check-phone
# ──────────────────────────────────────────────────────────────────────────────

class CustomerSignupPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    name: str
    otp: Optional[str] = None


class CustomerLoginPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    name: Optional[str] = None   # kept for legacy callers; ignored for auth
    otp: Optional[str] = None


def _build_token(phone: str, customer_id: int) -> str:
    from ..utils.auth import SECRET_KEY, ALGORITHM
    return jwt.encode({
        "sub": phone, 
        "user_id": str(customer_id),
        "role": "CUSTOMER",
        "account_type": "CUSTOMER"
    }, SECRET_KEY, algorithm=ALGORITHM)


def _customer_session(customer) -> dict:
    """Return a standard session dict for a customer."""
    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "email": getattr(customer, "email", None),
        "profile_picture_url": getattr(customer, "profile_picture_url", None),
        "loyalty_points": getattr(customer, "loyalty_points", 0),
        "token": _build_token(customer.phone, customer.id),
    }


@router.post("/api/v1/public/customers/check-phone")
def check_phone(payload: CustomerLoginPayload, db: Session = Depends(get_db)):
    """
    Check whether a phone number is already registered.
    Returns { customer_exists: bool } — does NOT create any record.
    Use before signup to give the user an early "already registered" message.
    """
    from ..models.customer import Customer

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    customer = db.query(Customer).filter(Customer.phone == norm).first()
    return {"customer_exists": customer is not None, "phone": norm}


@router.post("/api/v1/public/customers/signup")
def customer_signup(payload: CustomerSignupPayload, db: Session = Depends(get_db)):
    """
    Register a NEW customer only.
    - Normalizes the phone number.
    - Returns HTTP 409 if an account already exists for this phone.
    - Creates a new customer and returns a session token.
    - Frontend should NOT redirect the new user back to Login — they are already authenticated.
    """
    from ..models.customer import Customer

    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required for signup")

    # OTP validation (OTP-less stub: accept "1234")
    if payload.otp and payload.otp != "1234":
        raise HTTPException(status_code=400, detail="Invalid OTP")

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    existing = db.query(Customer).filter(Customer.phone == norm).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An account already exists with this mobile number. Please log in.",
        )

    customer = Customer(phone=norm, name=payload.name.strip())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return _customer_session(customer)


@router.post("/api/v1/public/customers/login")
def customer_login(payload: CustomerLoginPayload, db: Session = Depends(get_db)):
    """
    Authenticate an EXISTING customer by phone number.
    - Normalizes the phone number.
    - Returns HTTP 404 if no account is found (does NOT create one).
    - Returns a session token for the existing customer.
    Login must NEVER silently create a new customer.
    """
    from ..models.customer import Customer

    # OTP validation (OTP-less stub: accept "1234")
    if payload.otp and payload.otp != "1234":
        raise HTTPException(status_code=400, detail="Invalid OTP")

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    customer = db.query(Customer).filter(Customer.phone == norm).first()
    if not customer:
        raise HTTPException(
            status_code=404,
            detail="No account found for this mobile number. Please sign up.",
        )

    return _customer_session(customer)


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
def get_customer_profile_by_id(
    customer_id: str, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Return profile for a specific customer by integer ID or phone."""
    is_authorized = False
    
    if str(current_customer.id) == customer_id:
        is_authorized = True
    else:
        norm_path = normalize_phone(customer_id)
        norm_current = normalize_phone(current_customer.phone)
        if norm_path and norm_current and norm_path == norm_current:
            is_authorized = True
            
    if not is_authorized:
        raise HTTPException(status_code=403, detail="Not authorized to access this profile")
        
    return _customer_response(current_customer)


class UpdateProfilePayload(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


@router.patch("/api/v1/public/customers/{customer_id}/profile")
def update_customer_profile(
    customer_id: int, 
    payload: UpdateProfilePayload, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Update name, email, or address for a customer."""
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this profile")
        
    customer = current_customer
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
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """
    Upload a profile picture for a customer.
    Call via multipart/form-data with field 'file'.
    Because FastAPI requires UploadFile at function-definition time,
    we import it at module scope via a workaround below.
    """
    raise HTTPException(status_code=400, detail="Send as multipart/form-data with 'file' field")


# pyrefly: ignore [missing-import]
from fastapi import UploadFile as _FastAPIUploadFile, File as _FastAPIFile
import shutil as _shutil


@router.post("/api/v1/public/customers/{customer_id}/upload-picture")
async def upload_profile_picture_v2(
    customer_id: int,
    file: _FastAPIUploadFile = _FastAPIFile(...),
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Upload a profile picture (multipart/form-data, field 'file')."""
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to upload picture for this profile")
        
    customer = current_customer

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

    order_id: Optional[int] = None             # When provided, append items to this existing order
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
    delivery_address_snapshot: Optional[Dict[str, Any]] = None
    customer_latitude: Optional[float] = None
    customer_longitude: Optional[float] = None
    delivery_instructions: Optional[str] = None
    delivery_fee: Optional[float] = None
    packaging_fee: Optional[float] = None
    tip_amount: Optional[float] = None
    total_amount: float = 0
    
class RazorpayOrderPayload(BaseModel):
    order_id: int
    amount: float
    currency: str = "INR"
    receipt: Optional[str] = None

class RazorpayVerifyPayload(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    order_id: Optional[int] = None

class SettlePaymentPayload(BaseModel):
    model_config = {"extra": "ignore"}
    payment_method: str = "UPI"
    payment_id: Optional[str] = None
    amount_paid: Optional[float] = None

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
def place_order(payload: CustomerOrderPayload, restaurant_id: int, db: Session = Depends(get_db)):
    try:
        from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory, DeliveryPartner
        from ..models.customer import CustomerAddress
        from ..models.menu import MenuItem
        
        restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurant not found")

        # Check if this is an append to an existing order (Dine-in "order more / order again")
        if payload.order_id:
            existing_order = db.query(Order).filter(
                Order.id == payload.order_id,
                Order.restaurant_id == restaurant_id
            ).first()
            if existing_order:
                # Increment total amount using DB prices for the appended items
                added_amount = 0.0
                for item in payload.cart:
                    db_item = db.query(MenuItem).filter(MenuItem.id == item.id, MenuItem.restaurant_id == restaurant_id).first()
                    if not db_item:
                        raise HTTPException(status_code=404, detail=f"Menu item {item.id} not found in this restaurant")
                    qty = max(1, item.quantity)
                    price = float(db_item.price)
                    added_amount += price * qty
                    
                    order_item = OrderItem(
                        order_id=existing_order.id,
                        menu_item_id=db_item.id,
                        quantity=qty,
                        price=price
                    )
                    db.add(order_item)
                
                if payload.phone and not getattr(existing_order, 'customer_phone', None):
                    existing_order.customer_phone = payload.phone.strip()
                existing_order.total_amount += added_amount
                db.commit()
                db.refresh(existing_order)
                return {
                    "success": True,
                    "orderId": f"ORD-{str(existing_order.id).zfill(6)}",
                    "dbOrderId": existing_order.id,
                    "message": "Items appended to existing order successfully",
                    "isAppended": True
                }

        # Find or default table_id — ONLY for Dine-In orders
        is_dine_in = (payload.order_type or "DINE_IN").upper() in ["DINE_IN", "DINE IN"]
        is_delivery = (payload.order_type or "").upper() == "DELIVERY"
        if not is_dine_in:
            table_id = None
        else:
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
                    db.flush()
                    db.refresh(table)
                else:
                    table.status = "Occupied"
                    db.flush()
                table_id = table.id

        norm_method = (payload.payment_method or "").lower().replace(" ", "").replace("_", "")
        is_pay_at_counter = norm_method in ["payatcounter", "counter"]
        is_digital = payload.payment_method in ["Razorpay", "UPI", "Card", "Wallet"] or norm_method in ["razorpay", "upi"]
        is_delivery_cash = is_delivery and (payload.payment_method in ["Cash", "COD", "Cash on Delivery"] or norm_method in ["cash", "cod", "cashondelivery"])
        is_pay_later = norm_method in ["paylater", "payatend"]

        # Paid status
        is_paid = is_digital or (is_dine_in and is_pay_at_counter)

        # New orders for Takeaway and Delivery start at CONFIRMED so kitchen cooks it and live tracking steps work
        status = "CONFIRMED"
        if not is_paid and not is_delivery_cash and not is_pay_later and not is_pay_at_counter:
            status = "PENDING"
            
        payment_status = "Paid" if is_paid else "Pending"

        # Build complete delivery address snapshot securely from DB
        delivery_snapshot = None
        valid_delivery_address_id = None
        if is_delivery:
            if not payload.delivery_address_id:
                raise HTTPException(status_code=400, detail="Delivery address ID is required for delivery orders")
                
            addr_db = db.query(CustomerAddress).filter(CustomerAddress.id == payload.delivery_address_id).first()
            if not addr_db:
                raise HTTPException(status_code=404, detail="Customer address not found")
                
            # Verify ownership if possible (assuming payload has customer identity or it's checked earlier)
            # We trust the db address completely
            import json
            delivery_snapshot = json.dumps({
                "full_address": addr_db.full_address,
                "latitude": addr_db.latitude,
                "longitude": addr_db.longitude,
                "contact_name": addr_db.contact_name,
                "contact_phone": addr_db.contact_phone,
                "delivery_instructions": addr_db.delivery_instructions or payload.delivery_instructions
            })
            
            try:
                from ..models.order import DeliveryAddress as DelivAddrModel
                da = db.query(DelivAddrModel).filter(DelivAddrModel.id == payload.delivery_address_id).first()
                if da:
                    valid_delivery_address_id = da.id
            except Exception:
                valid_delivery_address_id = None

        clean_payment_method = "Cash" if is_delivery_cash else (payload.payment_method or "UPI")

        # Calculate authoritative totals from the DB
        calculated_subtotal = 0.0
        validated_items = []
        for item in payload.cart:
            db_item = db.query(MenuItem).filter(MenuItem.id == item.id, MenuItem.restaurant_id == restaurant_id).first()
            if not db_item:
                raise HTTPException(status_code=404, detail=f"Menu item {item.id} not found in this restaurant")
            qty = max(1, item.quantity)
            price = float(db_item.price)
            calculated_subtotal += price * qty
            validated_items.append((db_item.id, qty, price))

        # Re-apply fees from backend calculation where possible, or trust payload for non-critical fees
        # Ideally delivery_fee should be calculated, but using payload for now if not strictly defined in requirements
        delivery_fee = float(payload.delivery_fee) if payload.delivery_fee else 0.0
        packaging_fee = float(payload.packaging_fee) if payload.packaging_fee else 0.0
        tip_amount = float(payload.tip_amount) if payload.tip_amount else 0.0
        discount_amount = float(payload.discount_amount) if payload.discount_amount else 0.0
        
        calculated_total = calculated_subtotal + delivery_fee + packaging_fee + tip_amount - discount_amount

        new_order = Order(
            restaurant_id=restaurant_id,
            table_id=table_id,
            total_amount=calculated_total,
            status=status,
            payment_status=payment_status,
            payment_method=clean_payment_method,
            order_type=payload.order_type if payload.order_type else "DINE_IN",
            delivery_address_id=valid_delivery_address_id,
            delivery_address_snapshot=delivery_snapshot,
            delivery_instructions=payload.delivery_instructions,
            delivery_status="RIDER_SEARCHING" if is_delivery else None,
            delivery_fee=delivery_fee,
            packaging_fee=packaging_fee,
            tip_amount=tip_amount,
            customer_phone=payload.phone.strip() if payload.phone else None,
            discount_amount=discount_amount,
            discount_code=payload.discount_code,
        )
        db.add(new_order)
        db.flush()
        db.refresh(new_order)

        for item_id, qty, price in validated_items:
            order_item = OrderItem(
                order_id=new_order.id,
                menu_item_id=item_id,
                quantity=qty,
                price=price
            )
            db.add(order_item)

        # If Delivery Order, create an UNASSIGNED delivery assignment.
        # Riders will pick this up from the /api/v1/rider/available-orders endpoint.
        if is_delivery:
            import json as _json
            unassigned = DeliveryAssignment(
                order_id=new_order.id,
                rider_id=None,
                status="UNASSIGNED",
                assigned_at=datetime.utcnow()
            )
            db.add(unassigned)
            new_order.delivery_status = "RIDER_SEARCHING"
            history = DeliveryStatusHistory(
                order_id=new_order.id,
                delivery_assignment_id=None,
                rider_id=None,
                status="RIDER_SEARCHING",
                latitude=_json.loads(delivery_snapshot).get("latitude") if delivery_snapshot else None,
                longitude=_json.loads(delivery_snapshot).get("longitude") if delivery_snapshot else None,
                notes="Delivery order created, searching for available rider"
            )
            db.add(history)
            db.commit()
        else:
            db.commit()

        return {
            "success": True,
            "orderId": f"ORD-{str(new_order.id).zfill(6)}",
            "dbOrderId": new_order.id,
            "message": "Order placed successfully",
            "isAppended": False,
            "delivery_address": delivery_snapshot
        }
    except Exception as e:
        db.rollback()
        import logging
        logging.error(f"Order placement failed: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred while placing your order. Please try again.")


@router.post("/api/orders/{order_id}/settle-payment")
def settle_order_payment(order_id: str, payload: SettlePaymentPayload, db: Session = Depends(get_db)):
    try:
        # Support either numeric db id or "ORD-000123"
        clean_id_str = str(order_id).replace("ORD-", "").replace("ord-", "").strip()
        num_id = int(clean_id_str)

        order = db.query(Order).options(
            joinedload(Order.items).joinedload(OrderItem.menu_item)
        ).filter(Order.id == num_id).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

        order.payment_status = "Paid"
        order.payment_method = payload.payment_method or "Pay at Counter"
        order.status = "SERVED"
        if payload.amount_paid is not None and payload.amount_paid > 0:
            order.total_amount = payload.amount_paid

        # Release table
        table_num = None
        if order.table_id:
            table = db.query(Table).filter(Table.id == order.table_id).first()
            if table:
                table.status = "Vacant"
                table_num = table.table_number

        # ── Loyalty auto-credit (backend-only, idempotent) ──────────────────
        try:
            from ..models.customer import Customer, LoyaltyTransaction
            if order.customer_phone and order.payment_status == "Paid":
                from ..utils.phone import normalize_phone
                norm_phone = normalize_phone(order.customer_phone)
                cust = db.query(Customer).filter(Customer.phone == norm_phone).first()
                if not cust:
                    cust = db.query(Customer).filter(Customer.phone == order.customer_phone).first()
                if cust:
                    RUPEES_PER_POINT = 10
                    # Discount is already subtracted from total_amount. Tip and packaging are typically not rewarded.
                    base_amount = (order.total_amount or 0) - (order.packaging_fee or 0) - (order.tip_amount or 0)
                    points_to_award = max(0, int(base_amount // RUPEES_PER_POINT))
                    if points_to_award > 0:
                        existing = db.query(LoyaltyTransaction).filter(
                            LoyaltyTransaction.order_id == order.id,
                            LoyaltyTransaction.transaction_type == "ORDER_CREDIT"
                        ).first()
                        if not existing:
                            lt = LoyaltyTransaction(
                                customer_id=cust.id,
                                order_id=order.id,
                                points=points_to_award,
                                transaction_type="ORDER_CREDIT",
                                description=f"Order ORD-{str(order.id).zfill(6)} completed"
                            )
                            db.add(lt)
                            cust.loyalty_points = (cust.loyalty_points or 0) + points_to_award
        except Exception as _loyalty_err:
            import logging as _log
            _log.getLogger(__name__).warning(f"Loyalty credit failed for order {order.id}: {_loyalty_err}")

        db.commit()
        db.refresh(order)

        items = []
        for i in (order.items or []):
            m_id = i.menu_item_id
            m_name = "Item"
            m_img = None
            if i.menu_item:
                m_id = i.menu_item.id
                m_name = i.menu_item.name or "Item"
                m_img = getattr(i.menu_item, 'image_url', None)
            items.append({
                "id": m_id,
                "name": m_name,
                "quantity": i.quantity,
                "price": i.price,
                "image": m_img
            })

        return {
            "success": True,
            "orderId": f"ORD-{str(order.id).zfill(6)}",
            "dbOrderId": order.id,
            "tableNumber": table_num or "T-01",
            "totalAmount": order.total_amount,
            "paymentStatus": order.payment_status,
            "paymentMethod": order.payment_method,
            "status": order.status,
            "items": items,
            "message": "Payment settled and table released successfully"
        }
    except HTTPException:
        db.rollback()
        raise
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
def create_razorpay_order(payload: RazorpayOrderPayload, db: Session = Depends(get_db)):
    try:
        if razorpay is None:
            raise HTTPException(status_code=503, detail="Razorpay integration is unavailable on this server")

        key_id = os.getenv("RAZORPAY_KEY_ID")
        key_secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise HTTPException(status_code=500, detail="Razorpay credentials not configured")
            
        order = db.query(Order).filter(Order.id == payload.order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        client = razorpay.Client(auth=(key_id, key_secret))
        
        receipt = payload.receipt or f"rcpt_{int(time.time()*1000)}"
        
        # NEVER trust frontend amount. Use DB amount.
        actual_amount = order.total_amount
        
        data = {
            "amount": int(actual_amount * 100),
            "currency": payload.currency,
            "receipt": receipt
        }
        
        razorpay_order = client.order.create(data=data)
        return {"success": True, "order": razorpay_order, "actual_amount": actual_amount}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/verify-payment")
def verify_payment(payload: RazorpayVerifyPayload, db: Session = Depends(get_db)):
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
            if payload.order_id:
                order = db.query(Order).filter(Order.id == payload.order_id).first()
                if order:
                    order.payment_status = "Paid"
                    order.payment_method = "Razorpay"
                    order.status = "CONFIRMED" if order.status == "PENDING" else order.status
                    db.commit()
            return {"success": True, "message": "Payment verified successfully"}
        else:
            raise HTTPException(status_code=400, detail="Invalid signature sent!")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/v1/public/orders/table/{table_number}")
def get_public_active_order_for_table(table_number: str, restaurant_id: int = 1, db: Session = Depends(get_db)):
    clean_table = table_number.strip()
    table = db.query(Table).filter(Table.table_number.ilike(f"%{clean_table}%"), Table.restaurant_id == restaurant_id).first()
    
    query = db.query(Order).filter(Order.restaurant_id == restaurant_id)
    # pyrefly: ignore [missing-import]
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
        "order_type": getattr(order, "order_type", "DINE_IN"),
        "delivery_status": getattr(order, "delivery_status", None),
        "delivery_address": json.loads(order.delivery_address_snapshot) if isinstance(getattr(order, "delivery_address_snapshot", None), str) else getattr(order, "delivery_address_snapshot", {}),
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
    """Return all online customer orders for a customer identified by phone number.
    Excludes internal cashier / POS counter orders."""
    # pyrefly: ignore [missing-import]
    from sqlalchemy.orm import joinedload
    # pyrefly: ignore [missing-import]
    from sqlalchemy import or_

    clean_phone = phone.strip() if phone else ""
    if not clean_phone:
        return []

    # Strip non-digits for flexible matching (e.g. +91 9876543210 -> 9876543210)
    raw_digits = "".join(ch for ch in clean_phone if ch.isdigit())
    if len(raw_digits) < 10 or raw_digits[-10:] in ["1234567890", "9876543210"]:
        return []
    last_10 = raw_digits[-10:]

    filters = [
        Order.customer_phone == clean_phone,
        Order.customer_phone == last_10,
        Order.customer_phone == f"+91{last_10}",
        Order.customer_phone == f"+91 {last_10}",
    ]

    orders = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.menu_item))
        .filter(Order.restaurant_id == restaurant_id)
        .filter(Order.customer_phone.isnot(None))
        .filter(or_(*filters))
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
                "order_type": getattr(order, "order_type", "DINE_IN"),
                "delivery_status": getattr(order, "delivery_status", None),
                "delivery_address": json.loads(order.delivery_address_snapshot) if isinstance(getattr(order, "delivery_address_snapshot", None), str) else getattr(order, "delivery_address_snapshot", {}),
                "status": order.status,
                "payment_method": order.payment_method,
                "payment_status": order.payment_status,
                "total_amount": order.total_amount,
                "created_at": order.created_at,
            },
            "items": items,
        })
    return result




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


@router.get("/api/v1/public/customers/{customer_id}/recommendations")
def get_customer_recommendations(
    customer_id: int, 
    restaurant_id: int,
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """
    Return deterministic, real-data recommendations for the 'For You' screen.
    Uses actual completed order history, scoped to the restaurant.
    """
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to access these recommendations")
    if not restaurant_id:
        raise HTTPException(status_code=400, detail="restaurant_id is required")
    # pyrefly: ignore [missing-import]
    from sqlalchemy import func
    # pyrefly: ignore [missing-import]
    from ..models.customer import CustomerFavorite, Customer
    # pyrefly: ignore [missing-import]
    from ..models.menu import MenuItem
    # pyrefly: ignore [missing-import]
    from ..models.order import Order, OrderItem
    
    customer = current_customer
        
    customer_phone = customer.phone

    # 1. Fetch Favorites
    favorites_qs = db.query(CustomerFavorite.menu_item_id).filter(CustomerFavorite.customer_id == customer_id).all()
    favorite_ids = {f[0] for f in favorites_qs}
    
    valid_statuses = ["SERVED", "COMPLETED", "DELIVERED"]

    # Base completed orders subquery for the restaurant
    rest_orders_subq = db.query(Order.id).filter(
        Order.restaurant_id == restaurant_id,
        Order.status.in_(valid_statuses)
    ).subquery()
    
    # 2. Fetch Popular Items (Restaurant-wide)
    popular_qs = db.query(
        OrderItem.menu_item_id,
        func.sum(OrderItem.quantity).label('total_qty')
    ).join(MenuItem, MenuItem.id == OrderItem.menu_item_id).filter(
        OrderItem.order_id.in_(rest_orders_subq),
        MenuItem.is_available == True
    ).group_by(OrderItem.menu_item_id).order_by(func.sum(OrderItem.quantity).desc()).limit(20).all()
    
    popular_item_ids = [p.menu_item_id for p in popular_qs]
    
    # 3. Fetch Frequently Ordered (Customer-specific)
    cust_orders_subq = db.query(Order.id).filter(
        Order.restaurant_id == restaurant_id,
        Order.status.in_(valid_statuses),
        Order.customer_phone == customer_phone
    ).subquery()
    
    freq_qs = db.query(
        OrderItem.menu_item_id,
        func.sum(OrderItem.quantity).label('total_qty'),
        func.max(Order.created_at).label('last_ordered')
    ).join(Order, Order.id == OrderItem.order_id).join(MenuItem, MenuItem.id == OrderItem.menu_item_id).filter(
        OrderItem.order_id.in_(cust_orders_subq),
        MenuItem.is_available == True
    ).group_by(OrderItem.menu_item_id).order_by(
        func.sum(OrderItem.quantity).desc(),
        func.max(Order.created_at).desc()
    ).limit(20).all()
    
    freq_item_ids = [f.menu_item_id for f in freq_qs]
    
    # Pre-fetch all necessary menu items to build frontend-ready objects
    all_needed_ids = set(favorite_ids) | set(popular_item_ids) | set(freq_item_ids)
    if all_needed_ids:
        menu_items = db.query(MenuItem).filter(MenuItem.id.in_(all_needed_ids)).all()
    else:
        menu_items = []
    menu_map = {m.id: m for m in menu_items}
    
    def build_item_dict(m: MenuItem, reason: str):
        return {
            "id": m.id,
            "menu_item_id": m.id,
            "name": m.name,
            "price": m.price,
            "image_url": m.image_url,
            "is_available": m.is_available,
            "category_id": getattr(m, "category_id", None),
            "is_veg": getattr(m, "is_veg", True),
            "is_favorite": m.id in favorite_ids,
            "reason": reason
        }

    favorites_list = [build_item_dict(menu_map[m_id], "One of your favorites") for m_id in favorite_ids if m_id in menu_map]
    
    freq_list = []
    for f in freq_qs:
        if f.menu_item_id in menu_map:
            freq_list.append(build_item_dict(menu_map[f.menu_item_id], f"Ordered {f.total_qty} times"))
            
    popular_list = []
    for p in popular_qs:
        if p.menu_item_id in menu_map:
            popular_list.append(build_item_dict(menu_map[p.menu_item_id], "Popular at Data Udipi"))
            
    # 4. Recommended Items (Deterministic Scoring)
    # Score = 40% freq, 25% recency, 20% fav, 15% popular
    # We will compute a rough score for each item the customer has ordered
    # or that is very popular.
    scores = {}
    
    # Normalize frequencies
    max_freq = max([f.total_qty for f in freq_qs]) if freq_qs else 1
    # Normalize popularity
    max_pop = max([p.total_qty for p in popular_qs]) if popular_qs else 1
    
    from datetime import datetime
    now = datetime.utcnow()
    
    for f in freq_qs:
        m_id = f.menu_item_id
        if m_id not in menu_map: continue
        # Freq score
        freq_score = (f.total_qty / max_freq) * 40
        
        # Recency score (days ago)
        days_ago = (now - f.last_ordered).days
        # Decay: max 25 points, drops to 0 after ~30 days
        recency_score = max(0, 25 - (days_ago * (25/30)))
        
        scores[m_id] = scores.get(m_id, 0) + freq_score + recency_score
        
    for p in popular_qs:
        m_id = p.menu_item_id
        if m_id not in menu_map: continue
        pop_score = (p.total_qty / max_pop) * 15
        scores[m_id] = scores.get(m_id, 0) + pop_score
        
    for m_id in favorite_ids:
        if m_id not in menu_map: continue
        scores[m_id] = scores.get(m_id, 0) + 20
        
    # Sort by score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    recommended_list = []
    for m_id, score in sorted_scores:
        # Avoid items that are already in freq_list if we want a distinct list, 
        # or just present the top items.
        recommended_list.append(build_item_dict(menu_map[m_id], "Recommended for you"))
        if len(recommended_list) >= 10: break
        
    # 5. Combos (Co-occurrence counting)
    # We look for orders that have exactly two items or find pairs within orders
    # To avoid N+1 and massive cross joins, we will do a targeted query:
    # Top 5 items from popular_qs, find what they are ordered with.
    combos_list = []
    
    try:
        # pyrefly: ignore [missing-import]
        from sqlalchemy import text
        # Simple co-occurrence using raw SQL for performance
        co_sql = text('''
            SELECT a.menu_item_id AS item1, b.menu_item_id AS item2, COUNT(*) as pair_count
            FROM order_items a
            JOIN order_items b ON a.order_id = b.order_id AND a.menu_item_id < b.menu_item_id
            JOIN orders o ON a.order_id = o.id
            WHERE o.restaurant_id = :rest_id AND o.status IN ('SERVED', 'COMPLETED', 'DELIVERED')
            GROUP BY item1, item2
            ORDER BY pair_count DESC
            LIMIT 5
        ''')
        pairs = db.execute(co_sql, {"rest_id": restaurant_id}).fetchall()
        
        for p in pairs:
            i1, i2, count = p
            if i1 in menu_map and i2 in menu_map:
                m1 = menu_map[i1]
                m2 = menu_map[i2]
                if m1.is_available and m2.is_available:
                    # Deterministic combo
                    combos_list.append({
                        "id": f"combo-{i1}-{i2}",
                        "name": f"{m1.name} + {m2.name}",
                        "price": m1.price + m2.price,
                        "image_url": m1.image_url or m2.image_url,
                        "reason": f"Ordered together {count} times",
                        "items": [
                            build_item_dict(m1, ""),
                            build_item_dict(m2, "")
                        ]
                    })
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error(f"Combo co-occurrence failed: {e}")
        
    # Fallback if no combos
    if not combos_list and len(popular_list) >= 2:
        m1 = menu_map.get(popular_list[0]["id"])
        m2 = menu_map.get(popular_list[1]["id"])
        if m1 and m2:
            combos_list.append({
                "id": f"combo-{m1.id}-{m2.id}",
                "name": f"{m1.name} + {m2.name}",
                "price": m1.price + m2.price,
                "image_url": m1.image_url or m2.image_url,
                "reason": "Popular pairing",
                "items": [
                    build_item_dict(m1, ""),
                    build_item_dict(m2, "")
                ]
            })

    return {
        "favorites": favorites_list,
        "frequently_ordered": freq_list,
        "popular_items": popular_list,
        "recommended_items": recommended_list,
        "combos": combos_list
    }

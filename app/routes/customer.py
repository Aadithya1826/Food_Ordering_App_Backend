import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, and_, cast, String, text

logger = logging.getLogger(__name__)
from decimal import Decimal, ROUND_HALF_UP

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload, selectinload
from ..db import SessionLocal
from ..models.menu import MenuCategory, MenuItem
from ..models.restaurant import Restaurant
from ..models.order import Order, OrderItem
from ..models.table import Table
from ..models.dine_in_session import DineInSession
from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory
from ..models.customer import CustomerAddress, Customer, LoyaltyTransaction, CustomerFavorite
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import os
import re
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
# Customer Auth: Server-Issued OTP Challenges & Production-Safe Verification
# ──────────────────────────────────────────────────────────────────────────────
import secrets
import uuid
from datetime import timedelta
import logging

auth_logger = logging.getLogger(__name__)

class CustomerRequestOtpPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    purpose: Optional[str] = "LOGIN_SIGNUP"


class CustomerVerifyOtpPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    challenge_id: str
    otp: str


class CustomerSignupPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    name: str
    otp: Optional[str] = None
    challenge_id: Optional[str] = None
    verification_token: Optional[str] = None


class CustomerLoginPayload(BaseModel):
    model_config = {"extra": "ignore"}
    phone: str
    name: Optional[str] = None   # kept for legacy callers; ignored for auth
    otp: Optional[str] = None
    challenge_id: Optional[str] = None
    verification_token: Optional[str] = None


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


def _hash_otp(phone: str, otp: str, challenge_id: str) -> str:
    """Generate cryptographically secure HMAC hash for OTP challenge."""
    from ..utils.auth import SECRET_KEY
    msg = f"{phone}:{otp}:{challenge_id}"
    return hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _is_legacy_stub_allowed() -> bool:
    """Check if legacy stub OTP ('1234') is permitted during transition window."""
    env = os.getenv("APP_ENV", "development").lower()
    default_allowed = "true" if env != "production" else "false"
    return os.getenv("ALLOW_LEGACY_STUB_OTP", default_allowed).lower() == "true"


@router.post("/api/v1/public/customers/check-phone")
def check_phone(payload: CustomerLoginPayload, db: Session = Depends(get_db)):
    """
    Check whether a phone number is already registered.
    Returns { customer_exists: bool } — does NOT create any record.
    Use before signup to give the user an early 'already registered' message.
    """
    from ..models.customer import Customer

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    customer = db.query(Customer).filter(Customer.phone == norm).first()
    return {"customer_exists": customer is not None, "phone": norm}


@router.post("/api/v1/public/customers/auth/request-otp")
def request_customer_otp(payload: CustomerRequestOtpPayload, db: Session = Depends(get_db)):
    """
    Issue a new single-use server OTP challenge with short expiration (5 mins),
    attempt limits, and rate limiting.
    """
    from ..models.customer import CustomerOTPChallenge

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    now = datetime.utcnow()
    # Rate limit: Max 5 challenges per phone per 10 minutes
    ten_mins_ago = now - timedelta(minutes=10)
    recent_count = db.query(CustomerOTPChallenge).filter(
        CustomerOTPChallenge.phone == norm,
        CustomerOTPChallenge.created_at >= ten_mins_ago
    ).count()
    if recent_count >= 5:
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests. Please wait a few minutes before trying again."
        )

    # Cooldown: Minimum 20s between OTP requests
    twenty_secs_ago = now - timedelta(seconds=20)
    latest = db.query(CustomerOTPChallenge).filter(
        CustomerOTPChallenge.phone == norm
    ).order_by(CustomerOTPChallenge.created_at.desc()).first()
    if latest and latest.created_at >= twenty_secs_ago:
        raise HTTPException(
            status_code=429,
            detail="Please wait 20 seconds before requesting another OTP."
        )

    # Invalidate previous unused challenges for this phone
    db.query(CustomerOTPChallenge).filter(
        CustomerOTPChallenge.phone == norm,
        CustomerOTPChallenge.is_used == False
    ).update({"is_used": True})

    # Generate cryptographically secure 6-digit OTP
    otp_code = f"{secrets.randbelow(900000) + 100000:06d}"
    challenge_id = f"otp_ch_{uuid.uuid4().hex}"
    otp_hash = _hash_otp(norm, otp_code, challenge_id)
    expires_at = now + timedelta(minutes=5)

    challenge = CustomerOTPChallenge(
        phone=norm,
        challenge_id=challenge_id,
        otp_hash=otp_hash,
        purpose=payload.purpose or "LOGIN_SIGNUP",
        attempts_left=3,
        is_used=False,
        expires_at=expires_at,
        created_at=now
    )
    db.add(challenge)
    db.commit()

    # Log securely for dispatch (SMS gateway integration point)
    auth_logger.info(f"[SECURE AUTH] Generated OTP challenge for {norm[-4:]}: challenge_id={challenge_id}")
    if os.getenv("APP_ENV", "development").lower() != "production":
        auth_logger.info(f"[DEV DEBUG OTP] Phone={norm} Code={otp_code}")

    return {
        "success": True,
        "challenge_id": challenge_id,
        "expires_in": 300,
        "message": "OTP challenge generated successfully"
    }


@router.post("/api/v1/public/customers/auth/verify-otp")
def verify_customer_otp(payload: CustomerVerifyOtpPayload, db: Session = Depends(get_db)):
    """
    Verify server-issued OTP challenge with attempt decrement, single-use,
    and issue a temporary verified claim token.
    """
    from ..models.customer import CustomerOTPChallenge, Customer
    from ..utils.auth import SECRET_KEY, ALGORITHM

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    challenge = db.query(CustomerOTPChallenge).filter(
        CustomerOTPChallenge.challenge_id == payload.challenge_id,
        CustomerOTPChallenge.phone == norm
    ).with_for_update().first()

    if not challenge:
        raise HTTPException(status_code=404, detail="Invalid OTP challenge or phone mismatch")

    if challenge.is_used:
        raise HTTPException(status_code=400, detail="This OTP challenge has already been used")

    now = datetime.utcnow()
    if now > challenge.expires_at:
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one")

    if challenge.attempts_left <= 0:
        raise HTTPException(status_code=400, detail="Too many failed attempts. Please request a new OTP")

    clean_otp = payload.otp.strip()
    expected_hash = _hash_otp(norm, clean_otp, challenge.challenge_id)
    if not hmac.compare_digest(challenge.otp_hash, expected_hash):
        challenge.attempts_left -= 1
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"Invalid OTP. {challenge.attempts_left} attempts remaining."
        )

    # Mark single-use challenge as used
    challenge.is_used = True
    db.commit()

    # Issue signed verification token valid for 10 minutes
    verification_token = jwt.encode({
        "sub": norm,
        "purpose": "OTP_VERIFIED",
        "challenge_id": challenge.challenge_id,
        "exp": int(time.time()) + 600
    }, SECRET_KEY, algorithm=ALGORITHM)

    existing_customer = db.query(Customer).filter(Customer.phone == norm).first()
    return {
        "success": True,
        "phone": norm,
        "verification_token": verification_token,
        "customer_exists": existing_customer is not None,
        "session": _customer_session(existing_customer) if existing_customer else None
    }


def _verify_auth_proof(norm_phone: str, payload: Any, db: Session) -> bool:
    """
    Verify authentication proof using:
    1. verification_token (signed by server after verify-otp)
    2. challenge_id + otp (validated on the fly)
    3. Legacy Play Store client compatibility (ALLOW_LEGACY_STUB_OTP window)
    """
    from ..models.customer import CustomerOTPChallenge
    from ..utils.auth import SECRET_KEY, ALGORITHM

    # 1. Verification token check
    v_token = getattr(payload, "verification_token", None)
    if v_token:
        try:
            claims = jwt.decode(v_token, SECRET_KEY, algorithms=[ALGORITHM])
            if claims.get("purpose") == "OTP_VERIFIED" and claims.get("sub") == norm_phone:
                return True
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid or expired verification token")

    # 2. On-the-fly challenge check
    c_id = getattr(payload, "challenge_id", None)
    otp = getattr(payload, "otp", None)
    if c_id and otp:
        challenge = db.query(CustomerOTPChallenge).filter(
            CustomerOTPChallenge.challenge_id == c_id,
            CustomerOTPChallenge.phone == norm_phone
        ).with_for_update().first()
        if challenge and not challenge.is_used and datetime.utcnow() <= challenge.expires_at and challenge.attempts_left > 0:
            expected_hash = _hash_otp(norm_phone, otp.strip(), c_id)
            if hmac.compare_digest(challenge.otp_hash, expected_hash):
                challenge.is_used = True
                db.commit()
                return True
            else:
                challenge.attempts_left -= 1
                db.commit()
                raise HTTPException(status_code=400, detail="Invalid OTP code")

    # 3. Compatible rollout check for published Play Store client
    if _is_legacy_stub_allowed() and otp == "1234":
        return True

    raise HTTPException(
        status_code=400,
        detail="Valid OTP verification required. Please request an OTP challenge."
    )


@router.post("/api/v1/public/customers/signup")
def customer_signup(payload: CustomerSignupPayload, db: Session = Depends(get_db)):
    """
    Register a NEW customer only after verified authentication.
    - Normalizes the phone number.
    - Requires verified OTP proof.
    - Returns HTTP 409 if an account already exists for this phone.
    - Creates a new customer and returns a session token.
    """
    from ..models.customer import Customer

    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=422, detail="Name is required for signup")

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    # Authenticate proof
    _verify_auth_proof(norm, payload, db)

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
    Authenticate an EXISTING customer by phone number after verified authentication.
    - Normalizes the phone number.
    - Requires verified OTP proof.
    - Returns HTTP 404 if no account is found (does NOT create one).
    - Returns a session token for the existing customer.
    """
    from ..models.customer import Customer

    norm = normalize_phone(payload.phone.strip())
    if not norm:
        raise HTTPException(status_code=422, detail="Invalid phone number")

    # Authenticate proof
    _verify_auth_proof(norm, payload, db)

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
    session_id: Optional[int] = None           # When provided, link order to this Dine-In session
    session_uid: Optional[str] = None          # e.g. "SES-001001"
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
    client_order_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    timestamp: Optional[Any] = None
    original_timestamp: Optional[Any] = None
    
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

class ActiveDineInSessionPayload(BaseModel):
    model_config = {"extra": "ignore"}
    restaurant_id: int
    table_number: str
    phone: Optional[str] = None

class SettleDineInSessionPayload(BaseModel):
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
def place_order(payload: CustomerOrderPayload, restaurant_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory, DeliveryPartner
        from ..models.customer import CustomerAddress
        from ..models.menu import MenuItem
        
        restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
        if not restaurant:
            # If restaurant not found, check if items in cart belong to a valid restaurant
            if payload.cart and len(payload.cart) > 0:
                first_item = db.query(MenuItem).filter(MenuItem.id == payload.cart[0].id).first()
                if first_item:
                    restaurant = db.query(Restaurant).filter(Restaurant.id == first_item.restaurant_id).first()
                    if restaurant:
                        restaurant_id = restaurant.id
            if not restaurant:
                raise HTTPException(status_code=404, detail="Restaurant not found")

        # 1. Resolve Idempotency Keys (Header has priority, fallback to payload body)
        keys_to_check = set()
        if request:
            for h in ("Idempotency-Key", "idempotency-key", "idempotency_key"):
                h_val = request.headers.get(h)
                if h_val and h_val.strip():
                    keys_to_check.add(h_val.strip())
        if payload.client_order_id and str(payload.client_order_id).strip():
            keys_to_check.add(str(payload.client_order_id).strip())
        if payload.idempotency_key and str(payload.idempotency_key).strip():
            keys_to_check.add(str(payload.idempotency_key).strip())

        primary_idempotency_key = None
        if request and request.headers.get("Idempotency-Key"):
            primary_idempotency_key = request.headers.get("Idempotency-Key").strip()
        elif payload.client_order_id and str(payload.client_order_id).strip():
            primary_idempotency_key = str(payload.client_order_id).strip()
        elif keys_to_check:
            primary_idempotency_key = next(iter(keys_to_check))

        # Check if key already exists (Idempotency Guarantee)
        if keys_to_check:
            existing_order = db.query(Order).options(joinedload(Order.items)).filter(
                or_(
                    Order.idempotency_key.in_(keys_to_check),
                    Order.client_order_id.in_(keys_to_check)
                )
            ).first()
            if existing_order:
                return {
                    "success": True,
                    "orderId": f"ORD-{str(existing_order.id).zfill(6)}",
                    "dbOrderId": existing_order.id,
                    "bill_no": existing_order.bill_no,
                    "billNo": existing_order.bill_no,
                    "sessionId": existing_order.dine_in_session_id,
                    "sessionUid": None,
                    "totalAmount": existing_order.total_amount,
                    "sessionTotalAmount": existing_order.total_amount,
                    "message": "Order already placed (idempotent replay)",
                    "isAppended": False,
                    "delivery_address": existing_order.delivery_address_snapshot,
                    "idempotent": True,
                    "is_duplicate": True,
                    "idempotency_key": existing_order.idempotency_key or primary_idempotency_key,
                    "client_order_id": existing_order.client_order_id or primary_idempotency_key
                }

        # Resolve customer phone from authenticated token or payload
        cust_phone = (payload.phone or "").strip()
        try:
            caller = resolve_order_caller(request, db)
            if caller and caller.get("type") == "CUSTOMER" and caller.get("customer"):
                auth_phone = getattr(caller["customer"], "phone", None)
                if auth_phone:
                    cust_phone = auth_phone.strip()
        except Exception:
            pass

        # Parse normalized order type
        backend_order_type = (payload.order_type or "DINE_IN").upper().replace(" ", "_")
        is_dine_in = backend_order_type in ["DINE_IN", "DINE IN"]
        is_delivery = backend_order_type == "DELIVERY"

        # STRICT RESTRICTION: Block delivery orders if customer has an active dine-in session
        if is_delivery and cust_phone:
            active_sess = db.query(DineInSession).filter(
                DineInSession.customer_phone == cust_phone,
                DineInSession.restaurant_id == restaurant_id,
                DineInSession.status == "ACTIVE"
            ).first()
            if active_sess:
                has_active_orders = db.query(Order).filter(
                    Order.dine_in_session_id == active_sess.id,
                    Order.status.notin_(["CANCELLED", "COMPLETED"]),
                    Order.payment_status.notin_(["Paid", "PAID"])
                ).first() is not None
                if has_active_orders:
                    raise HTTPException(
                        status_code=400,
                        detail="Dine-In Order In Progress: You have an active dine-in order. Please complete your current dine-in order before proceeding with delivery."
                    )
                else:
                    active_sess.status = "CANCELLED"
                    db.flush()

            active_order = db.query(Order).filter(
                Order.customer_phone == cust_phone,
                Order.restaurant_id == restaurant_id,
                Order.order_type.in_(["Dine In", "DINE_IN", "DINEIN"]),
                Order.status.notin_(["CANCELLED", "COMPLETED"]),
                Order.payment_status.notin_(["Paid", "PAID"])
            ).first()
            if active_order:
                raise HTTPException(
                    status_code=400,
                    detail="Dine-In Order In Progress: You have an active dine-in order. Please complete your current dine-in order before proceeding with delivery."
                )

        # For DINE_IN, Order More MUST create a brand new Order row with status PENDING under the active session.
        # It must NEVER mutate the previous order's items or status.
        is_dine_in_req = is_dine_in
        if payload.order_id and is_dine_in_req:
            # Look up existing order to inherit table/session context if not passed
            prev_order = db.query(Order).filter(
                Order.id == payload.order_id,
                Order.restaurant_id == restaurant_id
            ).first()
            if prev_order:
                if not payload.table_number and prev_order.table:
                    payload.table_number = prev_order.table.table_number
                if not payload.session_id and prev_order.dine_in_session_id:
                    payload.session_id = prev_order.dine_in_session_id
                if not payload.phone and prev_order.customer_phone:
                    payload.phone = prev_order.customer_phone
            # Clear payload.order_id so execution falls through to create a brand new Order row!
            payload.order_id = None

        if payload.order_id:
            existing_order = db.query(Order).filter(
                Order.id == payload.order_id,
                Order.restaurant_id == restaurant_id
            ).with_for_update().first()
            if not existing_order:
                raise HTTPException(status_code=404, detail="Active order not found for this restaurant")

            curr_status = (existing_order.status or "").upper()
            curr_payment = (existing_order.payment_status or "").upper()
            if curr_status in ["CANCELLED"] or curr_payment in ["PAID"]:
                raise HTTPException(status_code=400, detail="This order is already settled or cancelled")

            if not payload.cart or len(payload.cart) == 0:
                raise HTTPException(status_code=400, detail="Cart is empty")

            added_amount = 0.0
            new_appended_items = []
            for item in payload.cart:
                db_item = db.query(MenuItem).filter(
                    MenuItem.id == item.id,
                    MenuItem.restaurant_id == restaurant_id
                ).first()
                if not db_item:
                    raise HTTPException(status_code=404, detail=f"Menu item {item.id} not found in this restaurant")
                qty = max(1, item.quantity)
                price = float(db_item.price)
                added_amount += price * qty
                
                order_item = OrderItem(
                    order_id=existing_order.id,
                    menu_item_id=db_item.id,
                    quantity=qty,
                    price=price,
                    status="PENDING"
                )
                db.add(order_item)
                new_appended_items.append(order_item)
            
            if payload.phone and not getattr(existing_order, 'customer_phone', None):
                existing_order.customer_phone = payload.phone.strip()
            existing_order.total_amount = float(existing_order.total_amount or 0.0) + added_amount
            existing_order.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(existing_order)

            all_items = db.query(OrderItem).options(
                joinedload(OrderItem.menu_item)
            ).filter(OrderItem.order_id == existing_order.id).all()

            serialized_items = [
                {
                    "id": oi.id,
                    "order_id": oi.order_id,
                    "menu_item_id": oi.menu_item_id,
                    "quantity": oi.quantity,
                    "price": oi.price,
                    "status": getattr(oi, "status", None) or "PENDING",
                    "name": oi.menu_item.name if oi.menu_item else "Unknown Item",
                    "image": getattr(oi.menu_item, "image_url", None)
                }
                for oi in all_items
            ]

            return {
                "success": True,
                "orderId": f"ORD-{str(existing_order.id).zfill(6)}",
                "dbOrderId": existing_order.id,
                "totalAmount": existing_order.total_amount,
                "status": existing_order.status,
                "payment_status": existing_order.payment_status,
                "items": serialized_items,
                "message": "Items appended successfully",
                "isAppended": True
            }

        # Table validation — ONLY for Dine-In orders
        if not is_dine_in:
            table_id = None
        else:
            raw_table = (payload.table_number or "").strip()
            if not raw_table or raw_table.lower() in ["takeaway", "delivery", "none", "null"]:
                raise HTTPException(
                    status_code=400,
                    detail="Select Your Table First: Please select your dining table before placing a dine-in order."
                )

            # Robust candidate lookup
            num_only = raw_table.lstrip("Tt-").lstrip("0") or "0"
            num_zero = raw_table.lstrip("Tt-")
            candidates = list(dict.fromkeys([
                raw_table,
                f"T-{num_zero}",
                f"T-{num_only}",
                num_zero,
                num_only,
            ]))

            table = None
            for candidate in candidates:
                table = db.query(Table).filter(
                    Table.table_number == candidate,
                    Table.restaurant_id == restaurant_id
                ).first()
                if table:
                    break

            if not table:
                table = db.query(Table).filter(
                    Table.table_number.ilike(f"%{raw_table}%"),
                    Table.restaurant_id == restaurant_id
                ).first()

            if not table:
                raise HTTPException(
                    status_code=400,
                    detail=f"Table '{raw_table}' does not exist for this restaurant. Please select a valid table."
                )

            if getattr(table, 'is_active', True) is False:
                raise HTTPException(
                    status_code=400,
                    detail=f"Table '{table.table_number}' is currently inactive. Please select an active dining table."
                )

            table.status = "Occupied"
            db.flush()
            table_id = table.id

        norm_method = (payload.payment_method or "").lower().replace(" ", "").replace("_", "")
        is_pay_at_counter = norm_method in ["payatcounter", "counter"]
        is_digital = payload.payment_method in ["Razorpay", "UPI", "Card", "Wallet"] or norm_method in ["razorpay", "upi"]
        is_delivery_cash = is_delivery and (payload.payment_method in ["Cash", "COD", "Cash on Delivery"] or norm_method in ["cash", "cod", "cashondelivery"])
        is_pay_later = norm_method in ["paylater", "payatend"]

        # Paid status: Dine-In pay at counter or pay later is NOT paid upon initial order placement.
        # It is paid upon final meal completion / counter settlement.
        is_paid = is_digital and not (is_dine_in and is_pay_at_counter)

        # Dine-In orders always start as PENDING for the kitchen.
        # Takeaway and Delivery start as CONFIRMED when paid, cash-on-delivery, etc.
        if is_dine_in:
            status = "PENDING"
        else:
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
                fallback_source = db.query(MenuItem).filter(MenuItem.id == item.id).first()
                if fallback_source:
                    db_item = db.query(MenuItem).filter(
                        MenuItem.name == fallback_source.name,
                        MenuItem.restaurant_id == restaurant_id,
                        MenuItem.is_deleted == False
                    ).first()
                if not db_item:
                    raise HTTPException(status_code=404, detail=f"Menu item {item.id} not found in this restaurant")
            qty = max(1, item.quantity)
            price = float(db_item.price)
            calculated_subtotal += price * qty
            validated_items.append((db_item.id, qty, price))

        # Delivery fee from payload or standard 100 base fee (only for DELIVERY)
        if is_delivery:
            delivery_fee = float(payload.delivery_fee) if payload.delivery_fee else 100.0
            if delivery_fee <= 0.0:
                delivery_fee = 100.0
        else:
            delivery_fee = 0.0
        packaging_fee = float(payload.packaging_fee) if payload.packaging_fee else 0.0
        tip_amount = float(payload.tip_amount) if payload.tip_amount else 0.0
        discount_amount = float(payload.discount_amount) if payload.discount_amount else 0.0
        
        calculated_total = calculated_subtotal + delivery_fee + packaging_fee + tip_amount - discount_amount

        # Dine-In session linking
        session_obj = None
        if is_dine_in and table_id:
            candidate_session = None
            if payload.session_id:
                candidate_session = db.query(DineInSession).filter(
                    DineInSession.id == payload.session_id,
                    DineInSession.restaurant_id == restaurant_id,
                    DineInSession.status == "ACTIVE"
                ).with_for_update().first()
            if not candidate_session and payload.session_uid:
                candidate_session = db.query(DineInSession).filter(
                    DineInSession.session_uid == payload.session_uid,
                    DineInSession.restaurant_id == restaurant_id,
                    DineInSession.status == "ACTIVE"
                ).with_for_update().first()
            if not candidate_session:
                # Find current active session for this table
                session_query = db.query(DineInSession).filter(
                    DineInSession.table_id == table_id,
                    DineInSession.restaurant_id == restaurant_id,
                    DineInSession.status == "ACTIVE"
                )
                if cust_phone:
                    candidate_session = session_query.filter(
                        DineInSession.customer_phone == cust_phone
                    ).with_for_update().first()
                if not candidate_session:
                    candidate_session = session_query.with_for_update().first()

            if candidate_session:
                # Strictly check if candidate_session has active (non-cancelled, unpaid) orders or is fresh (0 orders).
                # If all existing orders are CANCELLED or PAID, do NOT reuse this dead session!
                sess_orders = db.query(Order).filter(
                    Order.dine_in_session_id == candidate_session.id
                ).all()
                active_sess_orders = [
                    o for o in sess_orders 
                    if (o.status or "").upper() != "CANCELLED" and (o.payment_status or "").upper() != "PAID"
                ]
                if sess_orders and not active_sess_orders:
                    # All previous orders were cancelled (or settled). Mark session CANCELLED so it is never reused.
                    candidate_session.status = "CANCELLED"
                    candidate_session.updated_at = datetime.utcnow()
                    db.flush()
                    candidate_session = None
                else:
                    session_obj = candidate_session

            if not session_obj:
                # Create a fresh new active Dine-In session
                new_uid = f"SES-{int(time.time())}-{table_id}"
                session_obj = DineInSession(
                    session_uid=new_uid,
                    restaurant_id=restaurant_id,
                    table_id=table_id,
                    customer_phone=cust_phone if cust_phone else (payload.phone.strip() if payload.phone else None),
                    status="ACTIVE",
                    payment_status="Pending",
                    total_amount=0.0
                )
                db.add(session_obj)
                db.flush()
                db.refresh(session_obj)

        session_bill_no = None
        if is_dine_in_req and session_obj:
            existing_sibling_bill = db.query(Order.bill_no).filter(
                Order.dine_in_session_id == session_obj.id,
                Order.status != "CANCELLED",
                Order.bill_no.isnot(None)
            ).first()
            if existing_sibling_bill and existing_sibling_bill[0]:
                session_bill_no = existing_sibling_bill[0]
            else:
                max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == restaurant_id).scalar() or 0
                session_bill_no = (max_bill_no or 0) + 1
        else:
            max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == restaurant_id).scalar() or 0
            session_bill_no = (max_bill_no or 0) + 1

        from .orders import parse_business_timestamp
        raw_cust_ts = (
            (request.headers.get("X-Original-Timestamp") if request else None) or
            (request.headers.get("x-original-timestamp") if request else None) or
            (request.headers.get("X-Timestamp") if request else None) or
            (request.headers.get("x-timestamp") if request else None) or
            payload.timestamp or
            payload.original_timestamp
        )
        business_cust_time = parse_business_timestamp(raw_cust_ts)

        new_order = Order(
            restaurant_id=restaurant_id,
            table_id=table_id,
            dine_in_session_id=session_obj.id if session_obj else None,
            bill_no=session_bill_no,
            order_source="CUSTOMER_APP",
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
            customer_phone=cust_phone if cust_phone else (payload.phone.strip() if payload.phone else None),
            discount_amount=discount_amount,
            discount_code=payload.discount_code,
            idempotency_key=primary_idempotency_key,
            client_order_id=payload.client_order_id or primary_idempotency_key,
            created_at=business_cust_time if business_cust_time else datetime.utcnow()
        )
        try:
            db.add(new_order)
            db.flush()
            db.refresh(new_order)
        except Exception as _insert_err:
            from sqlalchemy.exc import IntegrityError
            if isinstance(_insert_err, IntegrityError) and keys_to_check:
                db.rollback()
                existing_order = db.query(Order).options(joinedload(Order.items)).filter(
                    or_(
                        Order.idempotency_key.in_(keys_to_check),
                        Order.client_order_id.in_(keys_to_check)
                    )
                ).first()
                if existing_order:
                    return {
                        "success": True,
                        "orderId": f"ORD-{str(existing_order.id).zfill(6)}",
                        "dbOrderId": existing_order.id,
                        "bill_no": existing_order.bill_no,
                        "billNo": existing_order.bill_no,
                        "sessionId": existing_order.dine_in_session_id,
                        "sessionUid": None,
                        "totalAmount": existing_order.total_amount,
                        "sessionTotalAmount": existing_order.total_amount,
                        "message": "Order already placed (idempotent replay)",
                        "isAppended": False,
                        "delivery_address": existing_order.delivery_address_snapshot,
                        "idempotent": True,
                        "is_duplicate": True,
                        "idempotency_key": existing_order.idempotency_key or primary_idempotency_key,
                        "client_order_id": existing_order.client_order_id or primary_idempotency_key
                    }
            raise _insert_err

        if session_bill_no and session_obj:
            db.query(Order).filter(
                Order.dine_in_session_id == session_obj.id,
                Order.bill_no.is_(None)
            ).update({Order.bill_no: session_bill_no}, synchronize_session=False)

        for item_id, qty, price in validated_items:
            order_item = OrderItem(
                order_id=new_order.id,
                menu_item_id=item_id,
                quantity=qty,
                price=price,
                status="PENDING"
            )
            db.add(order_item)

        if session_obj:
            # Authoritatively update session total_amount
            session_obj.total_amount = float(session_obj.total_amount or 0.0) + calculated_total
            if payload.phone and not session_obj.customer_phone:
                session_obj.customer_phone = payload.phone.strip()
            session_obj.updated_at = datetime.utcnow()

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

            # Automatically dispatch to the nearest available online rider
            try:
                from ..services.dispatch import dispatch_order
                dispatch_order(db, new_order.id)
            except Exception as _disp_err:
                logger.error(f"Failed to auto-dispatch order {new_order.id}: {_disp_err}")
        else:
            db.commit()

        return {
            "success": True,
            "orderId": f"ORD-{str(new_order.id).zfill(6)}",
            "dbOrderId": new_order.id,
            "bill_no": new_order.bill_no,
            "billNo": new_order.bill_no,
            "sessionId": session_obj.id if session_obj else None,
            "sessionUid": session_obj.session_uid if session_obj else None,
            "totalAmount": new_order.total_amount,
            "sessionTotalAmount": float(session_obj.total_amount) if session_obj else new_order.total_amount,
            "message": "Order placed successfully",
            "isAppended": False,
            "delivery_address": delivery_snapshot
        }
    except HTTPException:
        db.rollback()
        raise
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

        # Settle Dine-In session orders or standalone order
        sibling_orders = [order]
        session_bill_no = order.bill_no

        if order.dine_in_session_id:
            sibling_orders = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.dine_in_session_id == order.dine_in_session_id,
                Order.restaurant_id == order.restaurant_id
            ).all()

            if not session_bill_no:
                session_bill_no = next((o.bill_no for o in sibling_orders if o.bill_no), None)
            if not session_bill_no:
                max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == order.restaurant_id).scalar() or 0
                session_bill_no = (max_bill_no or 0) + 1

            for o in sibling_orders:
                if (o.status or "").upper() != "CANCELLED":
                    o.payment_status = "Paid"
                    o.payment_method = payload.payment_method or "Pay at Counter"
                    o.bill_no = session_bill_no
                    if (o.status or "").upper() in ["CONFIRMED", "PENDING", "PREPARING", "READY"]:
                        o.status = "SERVED"
                    o.updated_at = datetime.utcnow()
        else:
            order.payment_status = "Paid"
            order.payment_method = payload.payment_method or "Pay at Counter"
            order.status = "SERVED"
            if not order.bill_no:
                max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == order.restaurant_id).scalar() or 0
                order.bill_no = (max_bill_no or 0) + 1
            session_bill_no = order.bill_no

        if payload.amount_paid is not None and payload.amount_paid > 0:
            final_settle_total = payload.amount_paid
            if not order.dine_in_session_id:
                order.total_amount = payload.amount_paid
        else:
            final_settle_total = sum(
                float(o.total_amount or 0.0)
                for o in sibling_orders
                if (o.status or "").upper() != "CANCELLED"
            )

        if order.dine_in_session_id:
            session_obj = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
            if session_obj:
                session_obj.status = "COMPLETED"
                session_obj.payment_status = "Paid"
                session_obj.total_amount = final_settle_total
                session_obj.closed_at = datetime.utcnow()
                session_obj.updated_at = datetime.utcnow()

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
                    base_amount = (final_settle_total or 0) - (order.packaging_fee or 0) - (order.tip_amount or 0)
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
        for o in sibling_orders:
            if (o.status or "").upper() != "CANCELLED":
                for i in (o.items or []):
                    if (getattr(i, 'status', None) or "").upper() == "CANCELLED":
                        continue
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
                        "price": float(i.price),
                        "image": m_img
                    })

        return {
            "success": True,
            "orderId": f"ORD-{str(order.id).zfill(6)}",
            "dbOrderId": order.id,
            "bill_no": session_bill_no,
            "tableNumber": table_num or "T-01",
            "totalAmount": final_settle_total,
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


# ──────────────────────────────────────────────────────────────────────────────
# Dine-In Session-Based Endpoints (DINE-IN ONLY)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/dine-in/session/active")
def get_or_create_active_dine_in_session(
    payload: ActiveDineInSessionPayload, 
    db: Session = Depends(get_db)
):
    """
    Look up or initialize the active Dine-In session for a specific table.
    Enforces transactional concurrency and table row locking.
    """
    try:
        base_num = payload.table_number.replace("T-", "").replace("t-", "").strip()
        table = db.query(Table).filter(
            (Table.table_number == payload.table_number) |
            (Table.table_number == base_num) |
            (Table.table_number == f"T-{base_num}"),
            Table.restaurant_id == payload.restaurant_id
        ).with_for_update().first()

        if not table:
            table = Table(
                table_number=payload.table_number, 
                restaurant_id=payload.restaurant_id, 
                capacity=4, 
                status="Occupied"
            )
            db.add(table)
            db.flush()
            db.refresh(table)
        else:
            table.status = "Occupied"
            db.flush()

        session_obj = db.query(DineInSession).filter(
            DineInSession.table_id == table.id,
            DineInSession.restaurant_id == payload.restaurant_id,
            DineInSession.status == "ACTIVE"
        ).with_for_update().first()

        if session_obj:
            # Check if all orders under this session are cancelled or settled
            sess_orders = db.query(Order).filter(
                Order.dine_in_session_id == session_obj.id,
                Order.restaurant_id == payload.restaurant_id
            ).all()
            active_orders = [
                o for o in sess_orders 
                if (o.status or "").upper() != "CANCELLED" and (o.payment_status or "").upper() != "PAID"
            ]
            if sess_orders and not active_orders:
                session_obj.status = "CANCELLED"
                session_obj.updated_at = datetime.utcnow()
                db.flush()
                session_obj = None

        if not session_obj:
            new_uid = f"SES-{int(time.time())}-{table.id}"
            session_obj = DineInSession(
                session_uid=new_uid,
                restaurant_id=payload.restaurant_id,
                table_id=table.id,
                customer_phone=payload.phone.strip() if payload.phone else None,
                status="ACTIVE",
                payment_status="Pending",
                total_amount=0.0
            )
            db.add(session_obj)
            db.commit()
            db.refresh(session_obj)
        else:
            if payload.phone and not session_obj.customer_phone:
                session_obj.customer_phone = payload.phone.strip()
                db.commit()
                db.refresh(session_obj)

        # Retrieve all orders under this session
        session_orders = db.query(Order).options(
            joinedload(Order.items).joinedload(OrderItem.menu_item)
        ).filter(
            Order.dine_in_session_id == session_obj.id,
            Order.restaurant_id == payload.restaurant_id
        ).order_by(Order.id.asc()).all()

        active_orders = [o for o in session_orders if (o.status or "").upper() != "CANCELLED"]
        total_accumulated = sum(
            float(o.total_amount or 0.0) 
            for o in active_orders
        )

        display_orders = active_orders if active_orders else session_orders
        orders_data = []
        for idx, o in enumerate(display_orders, 1):
            orders_data.append({
                "round": idx,
                "id": o.id,
                "orderId": f"ORD-{str(o.id).zfill(6)}",
                "status": o.status,
                "payment_status": o.payment_status,
                "total_amount": float(o.total_amount or 0.0),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "items": [
                    {
                        "id": oi.id,
                        "menu_item_id": oi.menu_item_id,
                        "name": oi.menu_item.name if oi.menu_item else "Item",
                        "quantity": oi.quantity,
                        "price": float(oi.price),
                        "status": getattr(oi, "status", None) or "PENDING",
                        "image": getattr(oi.menu_item, "image_url", None) if oi.menu_item else None
                    }
                    for oi in o.items
                    if (o.status or "").upper() == "CANCELLED" or (getattr(oi, "status", None) or "").upper() != "CANCELLED"
                ]
            })

        return {
            "success": True,
            "sessionId": session_obj.id,
            "sessionUid": session_obj.session_uid,
            "tableNumber": table.table_number,
            "tableId": table.id,
            "status": session_obj.status,
            "paymentStatus": session_obj.payment_status,
            "totalAmount": total_accumulated,
            "orders": orders_data
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/dine-in/session/{session_id}/details")
def get_dine_in_session_details(
    session_id: str, 
    restaurant_id: int = 1, 
    db: Session = Depends(get_db)
):
    """
    Get all orders and combined bill for a Dine-In session by numeric ID or session_uid.
    """
    session_obj = None
    if session_id.isdigit():
        session_obj = db.query(DineInSession).filter(
            DineInSession.id == int(session_id),
            DineInSession.restaurant_id == restaurant_id
        ).first()
    if not session_obj:
        session_obj = db.query(DineInSession).filter(
            DineInSession.session_uid == session_id,
            DineInSession.restaurant_id == restaurant_id
        ).first()

    if not session_obj:
        raise HTTPException(status_code=404, detail="Dine-in session not found")

    table = db.query(Table).filter(Table.id == session_obj.table_id).first()
    table_num = table.table_number if table else "Table"

    session_orders = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    ).filter(
        Order.dine_in_session_id == session_obj.id,
        Order.restaurant_id == restaurant_id
    ).order_by(Order.id.asc()).all()

    active_orders = [o for o in session_orders if (o.status or "").upper() != "CANCELLED"]
    total_accumulated = sum(float(o.total_amount or 0.0) for o in active_orders)
    if session_orders and not active_orders and session_obj.status != "CANCELLED":
        session_obj.status = "CANCELLED"
        if table:
            table.status = "Vacant"
        db.commit()

    all_items_flat = []
    orders_data = []
    # If active orders exist, ONLY return active orders as rounds!
    # If all orders in the session were cancelled, return them so the screen can render the Cancelled Order Summary
    display_orders = active_orders if active_orders else session_orders
    for idx, o in enumerate(display_orders, 1):
        order_items = []
        is_order_cancelled = (o.status or "").upper() == "CANCELLED"
        for oi in o.items:
            is_item_cancelled = (getattr(oi, "status", None) or "").upper() == "CANCELLED"
            item_entry = {
                "id": oi.id,
                "order_id": oi.order_id,
                "menu_item_id": oi.menu_item_id,
                "name": oi.menu_item.name if oi.menu_item else "Item",
                "quantity": oi.quantity,
                "price": float(oi.price),
                "status": getattr(oi, "status", None) or "PENDING",
                "image": getattr(oi.menu_item, "image_url", None) if oi.menu_item else None
            }
            order_items.append(item_entry)
            if not is_order_cancelled and not is_item_cancelled:
                all_items_flat.append(item_entry)

        orders_data.append({
            "round": idx,
            "id": o.id,
            "orderId": f"ORD-{str(o.id).zfill(6)}",
            "status": o.status,
            "payment_status": o.payment_status,
            "total_amount": float(o.total_amount or 0.0),
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "items": order_items
        })

    # Latest active order id (for primary tracking display)
    latest_order = active_orders[-1] if active_orders else (session_orders[-1] if session_orders else None)
    latest_order_status = latest_order.status if latest_order else None
    if not active_orders and session_orders:
        latest_order_status = "CANCELLED"

    return {
        "success": True,
        "sessionId": session_obj.id,
        "sessionUid": session_obj.session_uid,
        "tableNumber": table_num,
        "tableId": session_obj.table_id,
        "status": session_obj.status,
        "paymentStatus": session_obj.payment_status,
        "totalAmount": total_accumulated,
        "latestOrderId": f"ORD-{str(latest_order.id).zfill(6)}" if latest_order else None,
        "latestDbOrderId": latest_order.id if latest_order else None,
        "latestOrderStatus": latest_order_status,
        "orders": orders_data,
        "allItems": all_items_flat
    }


@router.post("/api/dine-in/session/{session_id}/settle")
def settle_dine_in_session(
    session_id: str, 
    payload: SettleDineInSessionPayload, 
    restaurant_id: int = 1,
    db: Session = Depends(get_db)
):
    """
    Settle the entire Dine-In session and release the table.
    Marks session as COMPLETED and all non-cancelled orders as Paid.
    """
    try:
        session_obj = None
        if session_id.isdigit():
            session_obj = db.query(DineInSession).filter(
                DineInSession.id == int(session_id),
                DineInSession.restaurant_id == restaurant_id
            ).with_for_update().first()
        if not session_obj:
            session_obj = db.query(DineInSession).filter(
                DineInSession.session_uid == session_id,
                DineInSession.restaurant_id == restaurant_id
            ).with_for_update().first()

        if not session_obj:
            raise HTTPException(status_code=404, detail="Dine-in session not found")

        # Query all orders in this session
        session_orders = db.query(Order).filter(
            Order.dine_in_session_id == session_obj.id,
            Order.restaurant_id == restaurant_id
        ).with_for_update().all()

        # Idempotency: A settled bill must not be settled again
        if session_obj.status == "COMPLETED" and (session_obj.payment_status or "").lower() == "paid":
            existing_bill_no = next((o.bill_no for o in session_orders if o.bill_no), None)
            table = db.query(Table).filter(Table.id == session_obj.table_id).first()
            table_num = table.table_number if table else "Table"
            return {
                "success": True,
                "sessionId": session_obj.id,
                "sessionUid": session_obj.session_uid,
                "tableNumber": table_num,
                "totalAmount": float(session_obj.total_amount or 0.0),
                "bill_no": existing_bill_no,
                "paymentStatus": "Paid",
                "paymentMethod": getattr(session_obj, "payment_method", None) or payload.payment_method or "Pay at Counter",
                "message": "Session already settled (idempotent response)"
            }


        final_combined_total = sum(
            float(o.total_amount or 0.0) 
            for o in session_orders 
            if (o.status or "").upper() != "CANCELLED"
        )
        if payload.amount_paid is not None and payload.amount_paid > 0:
            final_combined_total = payload.amount_paid

        # Close session
        session_obj.status = "COMPLETED"
        session_obj.payment_status = "Paid"
        session_obj.total_amount = final_combined_total
        session_obj.closed_at = datetime.utcnow()
        session_obj.updated_at = datetime.utcnow()

        # Generate single authoritative bill number for this session (Section 9)
        existing_bill_no = next((o.bill_no for o in session_orders if o.bill_no), None)
        if existing_bill_no:
            session_bill_no = existing_bill_no
        else:
            max_bill_no = db.query(func.max(Order.bill_no)).scalar() or 0
            session_bill_no = (max_bill_no or 0) + 1

        # Update all session orders
        for o in session_orders:
            if (o.status or "").upper() != "CANCELLED":
                o.payment_status = "Paid"
                o.payment_method = payload.payment_method or "Pay at Counter"
                o.bill_no = session_bill_no
                if (o.status or "").upper() in ["CONFIRMED", "PENDING", "PREPARING", "READY"]:
                    o.status = "SERVED"
                o.updated_at = datetime.utcnow()

        # Release table to Vacant
        table = db.query(Table).filter(Table.id == session_obj.table_id).first()
        table_num = "Table"
        if table:
            table.status = "Vacant"
            table_num = table.table_number

        # ── Loyalty auto-credit for customer on combined session bill ──────────────────
        try:
            from ..models.customer import Customer, LoyaltyTransaction
            if session_obj.customer_phone:
                norm_phone = normalize_phone(session_obj.customer_phone)
                cust = db.query(Customer).filter(Customer.phone == norm_phone).first()
                if not cust:
                    cust = db.query(Customer).filter(Customer.phone == session_obj.customer_phone).first()
                if cust:
                    RUPEES_PER_POINT = 10
                    points_to_award = max(0, int(final_combined_total // RUPEES_PER_POINT))
                    if points_to_award > 0:
                        first_order_id = session_orders[0].id if session_orders else None
                        lt = LoyaltyTransaction(
                            customer_id=cust.id,
                            order_id=first_order_id,
                            points=points_to_award,
                            transaction_type="ORDER_CREDIT",
                            description=f"Dine-in Session {session_obj.session_uid} completed"
                        )
                        db.add(lt)
                        cust.loyalty_points = (cust.loyalty_points or 0) + points_to_award
        except Exception as _loyalty_err:
            import logging as _log
            _log.getLogger(__name__).warning(f"Loyalty credit failed for session {session_obj.session_uid}: {_loyalty_err}")

        db.commit()

        return {
            "success": True,
            "sessionId": session_obj.id,
            "sessionUid": session_obj.session_uid,
            "tableNumber": table_num,
            "totalAmount": final_combined_total,
            "bill_no": session_bill_no,
            "paymentStatus": "Paid",
            "paymentMethod": payload.payment_method or "Pay at Counter",
            "message": "Session settled and table released successfully"
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


class CancelOrderPayload(BaseModel):
    model_config = {"extra": "ignore"}
    reason: Optional[str] = "Customer requested cancellation"
    phone: Optional[str] = None
    session_uid: Optional[str] = None

class CancelItemPayload(BaseModel):
    model_config = {"extra": "ignore"}
    reason: Optional[str] = "Customer requested cancellation"
    session_uid: Optional[str] = None

def _to_decimal(val) -> Decimal:
    if val is None:
        return Decimal('0.00')
    return Decimal(str(val))

def _find_order_by_id_or_ref(db: Session, order_id_val: Any, restaurant_id: Optional[int] = None, for_update: bool = False, load_items: bool = False):
    """
    Locate an Order by numeric PK or branch_order_id, strictly enforcing restaurant_id isolation.
    Eliminates cross-restaurant fallback leaks.
    """
    clean_id = None
    s_val = str(order_id_val).strip()
    match = re.search(r'\d+', s_val)
    if match:
        try:
            clean_id = int(match.group())
        except Exception:
            clean_id = None

    if clean_id is None:
        return None

    q = db.query(Order)
    if load_items:
        q = q.options(selectinload(Order.items).selectinload(OrderItem.menu_item))

    # 1. If restaurant_id is specified (> 0), strictly restrict to that restaurant.
    if restaurant_id is not None and restaurant_id > 0:
        cand_q = q.filter(
            (Order.id == clean_id) | (Order.branch_order_id == clean_id),
            Order.restaurant_id == restaurant_id
        )
        if for_update:
            cand_q = cand_q.with_for_update()
        return cand_q.first()

    # 2. If restaurant_id is omitted, match by unique global PK only
    cand_q = q.filter(Order.id == clean_id)
    if for_update:
        cand_q = cand_q.with_for_update()
    return cand_q.first()


def resolve_order_caller(request: Request, db: Session) -> Optional[dict]:
    """
    Extract authenticated caller from Bearer token:
    Returns {"type": "STAFF", "user": user} or {"type": "CUSTOMER", "customer": cust} or None.
    """
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]

    if not token:
        return None

    from ..middleware.jwt_middleware import verify_jwt_token
    try:
        payload = verify_jwt_token(token)
    except Exception:
        return None

    role = payload.get("role")
    account_type = payload.get("account_type")

    # 1. Staff check
    if role in ["HOTEL_ADMIN", "SUPER_ADMIN", "CASHIER"] or account_type in ["STAFF", "ADMIN"]:
        from ..middleware import get_user_from_token
        try:
            user = get_user_from_token(token, db)
            if user:
                return {"type": "STAFF", "user": user}
        except Exception:
            pass

    # 2. Customer check
    customer_id_str = str(payload.get("user_id") or payload.get("sub") or payload.get("id"))
    if customer_id_str:
        from ..models.customer import Customer
        if customer_id_str.startswith("+") or not customer_id_str.isdigit():
            cust = db.query(Customer).filter(Customer.phone == customer_id_str).first()
        else:
            cust = db.query(Customer).filter(Customer.id == int(customer_id_str)).first()
        if cust:
            return {"type": "CUSTOMER", "customer": cust}

    return None


def authorize_order_access_for_cancellation(
    order: Order,
    caller: Optional[dict],
    session_uid: Optional[str],
    restaurant_id: Optional[int],
    db: Session
):
    """
    Enforce strict cancellation authorization:
    - Customer must own CUSTOMER_APP order.
    - Cashier POS orders cannot be cancelled by customer.
    - Dine-In guests require validated session_uid.
    - Cross-customer and cross-restaurant cancellations are rejected.
    """
    # Cross-restaurant check
    if restaurant_id and order.restaurant_id != restaurant_id:
        raise HTTPException(status_code=403, detail="Cross-restaurant cancellation prohibited")

    # 1. Staff access
    if caller and caller.get("type") == "STAFF":
        from ..utils.roles import require_restaurant_access
        user = caller["user"]
        require_restaurant_access(user, order.restaurant_id)
        return

    # 2. Authenticated Customer
    if caller and caller.get("type") == "CUSTOMER":
        cust = caller["customer"]
        # Cashier POS isolation
        if getattr(order, "order_source", None) == "CASHIER_POS":
            raise HTTPException(
                status_code=403,
                detail="Cancellation unavailable for orders placed directly at the cashier counter."
            )

        cust_phone = normalize_phone(cust.phone)
        order_phone = normalize_phone(order.customer_phone) if order.customer_phone else None

        is_owner = False
        if order_phone and cust_phone and order_phone == cust_phone:
            is_owner = True
        elif order.user_id == cust.id:
            is_owner = True
        elif order.dine_in_session_id:
            sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
            if sess and sess.customer_phone and normalize_phone(sess.customer_phone) == cust_phone:
                is_owner = True

        if not is_owner:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You do not have permission to manage this order"
            )
        return

    # 3. Dine-In Guest with validated session_uid
    if session_uid and order.dine_in_session_id:
        sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
        if sess and sess.session_uid == session_uid and sess.restaurant_id == order.restaurant_id:
            if getattr(order, "order_source", None) == "CASHIER_POS":
                raise HTTPException(
                    status_code=403,
                    detail="Cancellation unavailable for orders placed directly at the cashier counter."
                )
            return

    # 4. Unauthorized
    raise HTTPException(
        status_code=401,
        detail="Authentication required to manage or cancel this order"
    )


def recalculate_order_financials(order: Order, db: Session) -> Decimal:
    """
    Authoritative recalculation of order totals, proportional GST, and discounts
    using Decimal financial precision.
    """
    all_items = order.items or []
    active_items = [
        oi for oi in all_items 
        if (getattr(oi, "status", None) or "PENDING").upper() != "CANCELLED"
    ]

    # If all items are cancelled, cancel whole order
    if not active_items:
        order.status = "CANCELLED"
        order.total_amount = 0.0
        order.delivery_fee = 0.0
        order.packaging_fee = 0.0
        order.tip_amount = 0.0
        order.gst_amount = 0.0
        order.discount_amount = 0.0
        return Decimal('0.00')

    # Sum active items
    new_items_subtotal = Decimal('0.00')
    for oi in active_items:
        price = _to_decimal(oi.price)
        qty = _to_decimal(oi.quantity or 1)
        new_items_subtotal += price * qty

    # Calculate original items subtotal to compute proportional GST/discount
    original_subtotal = Decimal('0.00')
    for oi in all_items:
        original_subtotal += _to_decimal(oi.price) * _to_decimal(oi.quantity or 1)

    # Proportional GST adjustment
    if original_subtotal > Decimal('0.00') and order.gst_amount:
        effective_gst_rate = _to_decimal(order.gst_amount) / original_subtotal
        new_gst = (new_items_subtotal * effective_gst_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        new_gst = Decimal('0.00')

    # Proportional discount adjustment
    if original_subtotal > Decimal('0.00') and order.discount_amount and order.discount_amount > 0:
        discount_ratio = _to_decimal(order.discount_amount) / original_subtotal
        new_discount = (new_items_subtotal * discount_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        new_discount = Decimal('0.00')

    delivery_fee = _to_decimal(order.delivery_fee)
    packaging_fee = _to_decimal(order.packaging_fee)
    tip_amount = _to_decimal(order.tip_amount)

    new_total = new_items_subtotal + delivery_fee + packaging_fee + tip_amount + new_gst - new_discount
    if new_total < Decimal('0.00'):
        new_total = Decimal('0.00')

    new_total_rounded = new_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    order.total_amount = float(new_total_rounded)
    order.gst_amount = float(new_gst)
    order.discount_amount = float(new_discount)

    # Dine-In session update
    norm_type = (order.order_type or "").upper().replace(" ", "_")
    if norm_type in ["DINE_IN", "DINEIN"] and order.dine_in_session_id:
        sess = db.query(DineInSession).filter(
            DineInSession.id == order.dine_in_session_id
        ).with_for_update().first()
        if sess:
            remaining_orders = db.query(Order).filter(
                Order.dine_in_session_id == sess.id,
                Order.status != "CANCELLED"
            ).all()
            session_total = Decimal('0.00')
            for ro in remaining_orders:
                session_total += _to_decimal(ro.total_amount)
            sess.total_amount = float(session_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            if not remaining_orders:
                sess.status = "CANCELLED"
                table = db.query(Table).filter(Table.id == sess.table_id).first()
                if table:
                    table.status = "Vacant"
            sess.updated_at = datetime.utcnow()

    return new_total_rounded


@router.get("/api/orders/{order_id}/cancellation-eligibility")
@router.get("/api/v1/orders/{order_id}/cancellation-eligibility")
def check_cancellation_eligibility(
    order_id: str,
    request: Request,
    restaurant_id: Optional[int] = None,
    session_uid: Optional[str] = None,
    db: Session = Depends(get_db)
):
    caller = resolve_order_caller(request, db)
    order = _find_order_by_id_or_ref(db, order_id, restaurant_id, for_update=False, load_items=True)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    authorize_order_access_for_cancellation(order, caller, session_uid, restaurant_id, db)

    order_status = (order.status or "").upper()
    is_blocked = order_status in ["PREPARING", "READY", "SERVED", "DELIVERED", "COMPLETED", "CANCELLED"] or any(
        (getattr(item, "status", None) or "PENDING").upper() in ["PREPARING", "READY", "SERVED", "COMPLETED"]
        for item in order.items
    )

    items_data = []
    for item in order.items:
        i_status = (getattr(item, "status", None) or "PENDING").upper()
        items_data.append({
            "id": item.id,
            "name": item.menu_item.name if item.menu_item else "Item",
            "quantity": item.quantity,
            "price": item.price,
            "status": i_status,
            "eligible": i_status == "PENDING" and not is_blocked
        })

    eligible = not is_blocked and order_status in ["PENDING", "CONFIRMED"]
    return {
        "order_id": order.id,
        "eligible": eligible,
        "status": order.status,
        "reason": None if eligible else (
            "Order is already cancelled" if order_status == "CANCELLED"
            else "Cancellation unavailable. The kitchen has already started preparing this order."
        ),
        "items": items_data
    }


@router.post("/api/orders/{order_id}/cancel")
@router.post("/api/v1/orders/{order_id}/cancel")
def cancel_order(
    order_id: str,
    request: Request,
    payload: CancelOrderPayload = CancelOrderPayload(),
    restaurant_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Cancel an order before kitchen preparation begins.
    Enforces authorization, concurrency locking, and status transitions.
    """
    try:
        caller = resolve_order_caller(request, db)
        order = _find_order_by_id_or_ref(db, order_id, restaurant_id, for_update=True, load_items=True)

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Authorize caller
        authorize_order_access_for_cancellation(order, caller, payload.session_uid, restaurant_id, db)

        curr_status = (order.status or "").upper()
        if curr_status == "CANCELLED":
            return {
                "success": True,
                "message": "Order is already cancelled",
                "order_id": order.id,
                "status": "CANCELLED"
            }

        is_staff = caller and caller.get("type") == "STAFF"
        if not is_staff:
            # Check if kitchen has already started preparation (HTTP 409)
            if curr_status in ["PREPARING", "READY", "SERVED", "DELIVERED", "COMPLETED"]:
                raise HTTPException(
                    status_code=409,
                    detail="Cancellation unavailable. The kitchen has already started preparing this order."
                )

            # Check item-level preparation
            for oi in order.items:
                oi_status = (getattr(oi, "status", None) or "PENDING").upper()
                if oi_status in ["PREPARING", "READY", "SERVED", "COMPLETED"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Cancellation unavailable. The kitchen has already started preparing this order."
                    )
        else:
            if curr_status in ["SERVED", "DELIVERED", "COMPLETED"]:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot cancel an order that has already been served or completed."
                )

        norm_type = (order.order_type or "").upper().replace(" ", "_")

        # Delivery assignment safety check
        if norm_type == "DELIVERY":
            from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory, DeliveryPartner
            da = db.query(DeliveryAssignment).filter(DeliveryAssignment.order_id == order.id).with_for_update().first()
            if da:
                if da.status in ["PICKED_UP", "ON_THE_WAY", "ARRIVED_CUSTOMER", "DELIVERED"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Cancellation unavailable. The order is already picked up for delivery."
                    )
                if da.status not in ["DELIVERED", "CANCELLED"]:
                    da.status = "CANCELLED"
                    da.updated_at = datetime.utcnow()
                    # Only mark rider available if they have no other active assignments
                    if da.rider_id:
                        other_active = db.query(DeliveryAssignment).filter(
                            DeliveryAssignment.rider_id == da.rider_id,
                            DeliveryAssignment.id != da.id,
                            DeliveryAssignment.status.in_(["ASSIGNED", "ACCEPTED", "PICKED_UP", "ON_THE_WAY"])
                        ).first()
                        if not other_active:
                            rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == da.rider_id).first()
                            if rider and rider.is_online:
                                rider.is_available = True
                                rider.updated_at = datetime.utcnow()
                    history = DeliveryStatusHistory(
                        order_id=order.id,
                        delivery_assignment_id=da.id,
                        rider_id=da.rider_id,
                        status="CANCELLED",
                        notes=payload.reason or "Order cancelled before preparation"
                    )
                    db.add(history)
            order.delivery_status = "CANCELLED"

        # Proceed with cancellation
        order.status = "CANCELLED"
        for oi in order.items:
            oi.status = "CANCELLED"

        # Dine-In session handling
        if norm_type in ["DINE_IN", "DINEIN"] and order.dine_in_session_id:
            sess = db.query(DineInSession).filter(
                DineInSession.id == order.dine_in_session_id
            ).with_for_update().first()
            if sess:
                remaining_orders = db.query(Order).filter(
                    Order.dine_in_session_id == sess.id,
                    Order.id != order.id,
                    Order.status != "CANCELLED"
                ).all()
                sess.total_amount = sum(float(ro.total_amount or 0.0) for ro in remaining_orders)
                if not remaining_orders:
                    sess.status = "CANCELLED"
                    table = db.query(Table).filter(Table.id == sess.table_id).first()
                    if table:
                        table.status = "Vacant"
                sess.updated_at = datetime.utcnow()

        # Prepaid handling
        if (order.payment_status or "").upper() == "PAID":
            order.payment_status = "REFUND_PENDING"

        order.updated_at = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "message": "Order cancelled successfully",
            "order_id": order.id,
            "status": "CANCELLED",
            "payment_status": order.payment_status,
            "order_type": order.order_type
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/orders/{order_id}/items/{item_id}/cancel")
@router.post("/api/v1/orders/{order_id}/items/{item_id}/cancel")
def cancel_order_item(
    order_id: str,
    item_id: int,
    request: Request,
    payload: CancelItemPayload = CancelItemPayload(),
    restaurant_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Item-level cancellation before kitchen preparation starts.
    Authoritative recalculation with Decimal financial precision.
    """
    try:
        caller = resolve_order_caller(request, db)
        order = _find_order_by_id_or_ref(db, order_id, restaurant_id, for_update=True, load_items=True)

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        authorize_order_access_for_cancellation(order, caller, payload.session_uid, restaurant_id, db)

        is_staff = caller and caller.get("type") == "STAFF"
        curr_status = (order.status or "").upper()
        if not is_staff and curr_status in ["PREPARING", "READY", "SERVED", "DELIVERED", "COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=409,
                detail="Cancellation unavailable. The kitchen has already started preparing this order."
            )
        if curr_status in ["SERVED", "DELIVERED", "COMPLETED"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel an item from an already served or completed order."
            )

        item = db.query(OrderItem).filter(
            (OrderItem.id == item_id) | (OrderItem.menu_item_id == item_id),
            OrderItem.order_id == order.id,
            OrderItem.status != "CANCELLED"
        ).with_for_update().first()

        if not item:
            item = db.query(OrderItem).filter(
                (OrderItem.id == item_id) | (OrderItem.menu_item_id == item_id),
                OrderItem.order_id == order.id
            ).with_for_update().first()

        if not item:
            raise HTTPException(status_code=404, detail="Order item not found")

        item_status = (getattr(item, "status", None) or "PENDING").upper()
        if not is_staff and item_status in ["PREPARING", "READY", "SERVED", "COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=409,
                detail="Cancellation unavailable. This item has already been prepared or cancelled."
            )
        if item_status in ["SERVED", "COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel an item that is already served, completed, or cancelled."
            )

        item.status = "CANCELLED"

        # Authoritative recalculation of all financial fields
        recalculate_order_financials(order, db)

        if (order.payment_status or "").upper() == "PAID":
            order.payment_status = "REFUND_PENDING"

        order.updated_at = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "message": "Item cancelled successfully",
            "item_id": item.id,
            "item_status": "CANCELLED",
            "order_id": order.id,
            "order_total": order.total_amount,
            "order_status": order.status
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

        if hmac.compare_digest(payload.razorpay_signature, expected_sign):
            if payload.order_id:
                order = db.query(Order).filter(Order.id == payload.order_id).with_for_update().first()
                if order:
                    if (order.payment_status or "").upper() == "PAID":
                        return {"success": True, "message": "Payment already confirmed (idempotent)"}
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
def get_order_by_id(
    order_id: str, 
    request: Request,
    restaurant_id: Optional[int] = None, 
    session_uid: Optional[str] = None,
    db: Session = Depends(get_db)
):
    try:
        if order_id.startswith("ORD-"):
            parsed_id = int(order_id.replace("ORD-", ""))
        elif order_id.startswith("UDP-"):
            parsed_id = int(order_id.replace("UDP-", ""))
        else:
            parsed_id = int(order_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid order ID format")
        
    order_query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    ).filter(Order.id == parsed_id)
    
    # Strictly enforce restaurant isolation: Never fall back cross-branch if restaurant_id is provided
    if restaurant_id is not None and restaurant_id > 0:
        order = order_query.filter(Order.restaurant_id == restaurant_id).first()
    else:
        order = order_query.first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Order lookup authorization
    caller = resolve_order_caller(request, db)
    if caller and caller.get("type") == "CUSTOMER":
        cust = caller["customer"]
        cust_phone = normalize_phone(cust.phone)
        order_phone = normalize_phone(order.customer_phone) if order.customer_phone else None
        if order_phone and cust_phone and order_phone != cust_phone and order.user_id != cust.id:
            is_sess_match = False
            if order.dine_in_session_id:
                sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
                if sess and sess.customer_phone and normalize_phone(sess.customer_phone) == cust_phone:
                    is_sess_match = True
            if not is_sess_match:
                raise HTTPException(status_code=403, detail="Unauthorized: Access to this order is denied")
    elif not caller:
        # Cashier POS orders must not leak to unauthenticated public callers
        if getattr(order, "order_source", None) == "CASHIER_POS":
            if not session_uid or not order.dine_in_session_id:
                raise HTTPException(status_code=403, detail="Access to counter POS orders requires authentication")
            sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
            if not sess or sess.session_uid != session_uid or sess.restaurant_id != order.restaurant_id:
                raise HTTPException(status_code=403, detail="Access to counter POS orders requires authentication")

    restaurant_id = order.restaurant_id
    table_number_map = build_table_number_map(db, [order])


    # If this order belongs to a settled bill or dine-in session, aggregate all active sibling orders
    sibling_orders = [order]
    if (order.status or "").upper() != "CANCELLED":
        if order.dine_in_session_id:
            sibling_orders = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.dine_in_session_id == order.dine_in_session_id,
                Order.restaurant_id == restaurant_id,
                Order.status != "CANCELLED"
            ).order_by(Order.id.asc()).all()
            if not sibling_orders:
                sibling_orders = [order]
        elif order.bill_no:
            order_date = order.created_at.date() if order.created_at else None
            q = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.bill_no == order.bill_no,
                Order.restaurant_id == restaurant_id,
                Order.status != "CANCELLED"
            )
            if order_date:
                q = q.filter(func.date(Order.created_at) == order_date)
            sibling_orders = q.order_by(Order.id.asc()).all()
            if not sibling_orders:
                sibling_orders = [order]

    items = []
    items_subtotal = 0.0
    for ord_obj in sibling_orders:
        for oi in (ord_obj.items or []):
            oi_status = getattr(oi, "status", None) or ("CANCELLED" if (ord_obj.status or "").upper() == "CANCELLED" else "PENDING")
            if (ord_obj.status or "").upper() != "CANCELLED" and oi_status == "CANCELLED":
                continue
            qty = float(oi.quantity or 1)
            price = float(oi.price or 0.0)
            items_subtotal += price * qty
            items.append({
                "id": oi.id,
                "order_id": oi.order_id,
                "menu_item_id": oi.menu_item_id,
                "quantity": oi.quantity,
                "price": price,
                "name": oi.menu_item.name if oi.menu_item else None,
                "description": oi.menu_item.description if oi.menu_item else None,
                "image_url": oi.menu_item.image_url if oi.menu_item else None,
                "status": oi_status
            })

    total_delivery_fee = sum(float(getattr(o, "delivery_fee", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_packaging_fee = sum(float(getattr(o, "packaging_fee", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_tip_amount = sum(float(getattr(o, "tip_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_discount_amount = sum(float(getattr(o, "discount_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_gst_amount = sum(float(getattr(o, "gst_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")

    calculated_combined_total = round(max(0.0, items_subtotal + total_delivery_fee + total_packaging_fee + total_tip_amount + total_gst_amount - total_discount_amount), 2)
        
    order_dict = {
        "id": order.id,
        "orderId": f"ORD-{str(order.id).zfill(6)}",
        "bill_no": order.bill_no,
        "dine_in_session_id": order.dine_in_session_id,
        "restaurant_id": order.restaurant_id,
        "table_id": order.table_id,
        "table_number": resolve_order_table_number(order, table_number_map),
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "subtotal": round(items_subtotal, 2),
        "delivery_fee": round(total_delivery_fee, 2),
        "packaging_fee": round(total_packaging_fee, 2),
        "tip_amount": round(total_tip_amount, 2),
        "discount_amount": round(total_discount_amount, 2),
        "discount_code": order.discount_code,
        "gst_amount": round(total_gst_amount, 2),
        "total_amount": calculated_combined_total,
        "created_at": order.created_at,
        "updated_at": order.updated_at
    }

    return {"order": order_dict, "items": items}


# ──────────────────────────────────────────────
# Public endpoints used by Frontend_app
# ──────────────────────────────────────────────

@router.get("/api/v1/public/orders/{order_id}")
def get_public_order(
    order_id: str, 
    request: Request,
    restaurant_id: Optional[int] = None, 
    session_uid: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Fetch order details with authorization checks and strict branch isolation."""
    try:
        if order_id.startswith("ORD-"):
            parsed_id = int(order_id.replace("ORD-", ""))
        elif order_id.startswith("UDP-"):
            parsed_id = int(order_id.replace("UDP-", ""))
        else:
            parsed_id = int(order_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid order ID format")

    order_query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    ).filter(Order.id == parsed_id)
    
    # Strictly enforce restaurant isolation
    if restaurant_id is not None and restaurant_id > 0:
        order = order_query.filter(Order.restaurant_id == restaurant_id).first()
    else:
        order = order_query.first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Authorize lookup
    caller = resolve_order_caller(request, db)
    if caller and caller.get("type") == "CUSTOMER":
        cust = caller["customer"]
        cust_phone = normalize_phone(cust.phone)
        order_phone = normalize_phone(order.customer_phone) if order.customer_phone else None
        if order_phone and cust_phone and order_phone != cust_phone and order.user_id != cust.id:
            is_sess_match = False
            if order.dine_in_session_id:
                sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
                if sess and sess.customer_phone and normalize_phone(sess.customer_phone) == cust_phone:
                    is_sess_match = True
            if not is_sess_match:
                raise HTTPException(status_code=403, detail="Unauthorized: Access to this order is denied")
    elif not caller:
        # Counter POS orders must never leak to unauthenticated public callers
        if getattr(order, "order_source", None) == "CASHIER_POS":
            if not session_uid or not order.dine_in_session_id:
                raise HTTPException(status_code=403, detail="Access to counter POS orders requires authentication")
            sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
            if not sess or sess.session_uid != session_uid or sess.restaurant_id != order.restaurant_id:
                raise HTTPException(status_code=403, detail="Access to counter POS orders requires authentication")

    restaurant_id = order.restaurant_id

    table_number_map = build_table_number_map(db, [order])

    sibling_orders = [order]
    if (order.status or "").upper() != "CANCELLED":
        if order.dine_in_session_id:
            sibling_orders = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.dine_in_session_id == order.dine_in_session_id,
                Order.restaurant_id == restaurant_id,
                Order.status != "CANCELLED"
            ).order_by(Order.id.asc()).all()
            if not sibling_orders:
                sibling_orders = [order]
        elif order.bill_no:
            order_date = order.created_at.date() if order.created_at else None
            q = db.query(Order).options(
                joinedload(Order.items).joinedload(OrderItem.menu_item)
            ).filter(
                Order.bill_no == order.bill_no,
                Order.restaurant_id == restaurant_id,
                Order.status != "CANCELLED"
            )
            if order_date:
                q = q.filter(func.date(Order.created_at) == order_date)
            sibling_orders = q.order_by(Order.id.asc()).all()
            if not sibling_orders:
                sibling_orders = [order]

    items = []
    items_subtotal = 0.0
    for ord_obj in sibling_orders:
        for oi in (ord_obj.items or []):
            oi_status = getattr(oi, "status", None) or ("CANCELLED" if (ord_obj.status or "").upper() == "CANCELLED" else "PENDING")
            if (ord_obj.status or "").upper() != "CANCELLED" and oi_status == "CANCELLED":
                continue
            qty = float(oi.quantity or 1)
            price = float(oi.price or 0.0)
            items_subtotal += price * qty
            items.append({
                "id": oi.id,
                "order_id": oi.order_id,
                "menu_item_id": oi.menu_item_id,
                "quantity": oi.quantity,
                "price": price,
                "name": oi.menu_item.name if oi.menu_item else None,
                "description": oi.menu_item.description if oi.menu_item else None,
                "image_url": oi.menu_item.image_url if oi.menu_item else None,
                "status": oi_status
            })

    total_delivery_fee = sum(float(getattr(o, "delivery_fee", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_packaging_fee = sum(float(getattr(o, "packaging_fee", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_tip_amount = sum(float(getattr(o, "tip_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_discount_amount = sum(float(getattr(o, "discount_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")
    total_gst_amount = sum(float(getattr(o, "gst_amount", 0.0) or 0.0) for o in sibling_orders if (o.status or "").upper() != "CANCELLED")

    calculated_combined_total = round(max(0.0, items_subtotal + total_delivery_fee + total_packaging_fee + total_tip_amount + total_gst_amount - total_discount_amount), 2)

    order_dict = {
        "id": order.id,
        "orderId": f"ORD-{str(order.id).zfill(6)}",
        "bill_no": order.bill_no,
        "dine_in_session_id": order.dine_in_session_id,
        "restaurant_id": order.restaurant_id,
        "table_id": order.table_id,
        "table_number": resolve_order_table_number(order, table_number_map),
        "order_type": getattr(order, "order_type", "DINE_IN"),
        "delivery_status": getattr(order, "delivery_status", None),
        "delivery_address": json.loads(order.delivery_address_snapshot) if isinstance(getattr(order, "delivery_address_snapshot", None), str) else getattr(order, "delivery_address_snapshot", {}),
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "subtotal": round(items_subtotal, 2),
        "delivery_fee": round(total_delivery_fee, 2),
        "packaging_fee": round(total_packaging_fee, 2),
        "tip_amount": round(total_tip_amount, 2),
        "discount_amount": round(total_discount_amount, 2),
        "discount_code": order.discount_code,
        "gst_amount": round(total_gst_amount, 2),
        "total_amount": calculated_combined_total,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    return {"order": order_dict, "items": items}


@router.get("/api/v1/public/customers/orders")
def get_customer_orders_by_phone(
    phone: str, 
    restaurant_id: int = 1, 
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """
    Return all online customer orders for the authenticated customer only.
    Excludes internal cashier / POS counter orders and enforces strict privacy.
    """
    clean_phone = phone.strip() if phone else ""
    if not clean_phone:
        return []

    norm_req = normalize_phone(clean_phone)
    norm_curr = normalize_phone(current_customer.phone)
    if not norm_req or not norm_curr or norm_req != norm_curr:
        raise HTTPException(
            status_code=403, 
            detail="Unauthorized: Cannot access order history of another customer"
        )

    raw_digits = "".join(ch for ch in clean_phone if ch.isdigit())
    if len(raw_digits) < 10:
        return []
    last_10 = raw_digits[-10:]

    filters = [
        Order.customer_phone == clean_phone,
        Order.customer_phone == last_10,
        Order.customer_phone == f"+91{last_10}",
        Order.customer_phone == f"+91 {last_10}",
        Order.customer_phone == norm_curr,
    ]

    orders = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.menu_item))
        .filter(Order.restaurant_id == restaurant_id)
        .filter(or_(Order.order_source == "CUSTOMER_APP", Order.order_source.is_(None)))
        .filter(Order.customer_phone.isnot(None))
        .filter(or_(*filters))
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )


    table_number_map = build_table_number_map(db, orders)
    result = []
    grouped_dine_in = {}

    for order in orders:
        ord_type = (getattr(order, "order_type", "DINE_IN") or "DINE_IN").upper()
        is_dine_in = ord_type in ["DINE_IN", "DINE IN"]

        group_key = None
        if is_dine_in:
            if not order.bill_no:
                if order.dine_in_session_id:
                    sibling_bill = db.query(Order.bill_no).filter(
                        Order.dine_in_session_id == order.dine_in_session_id,
                        Order.bill_no.isnot(None)
                    ).first()
                    if sibling_bill and sibling_bill[0]:
                        order.bill_no = sibling_bill[0]
                    else:
                        max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == order.restaurant_id).scalar() or 0
                        order.bill_no = (max_bill_no or 0) + 1
                else:
                    max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == order.restaurant_id).scalar() or 0
                    order.bill_no = (max_bill_no or 0) + 1
                db.commit()

            if order.dine_in_session_id:
                group_key = f"dinein_sess_{order.restaurant_id}_{order.dine_in_session_id}"
            elif order.bill_no:
                group_key = f"dinein_bill_{order.restaurant_id}_{order.bill_no}"

        items = []
        for oi in order.items:
            oi_status = getattr(oi, "status", None) or ("CANCELLED" if (order.status or "").upper() == "CANCELLED" else "PENDING")
            if (order.status or "").upper() != "CANCELLED" and oi_status == "CANCELLED":
                continue
            items.append({
                "id": oi.id,
                "order_id": oi.order_id,
                "menu_item_id": oi.menu_item_id,
                "quantity": oi.quantity,
                "price": oi.price,
                "name": oi.menu_item.name if oi.menu_item else None,
                "image_url": oi.menu_item.image_url if oi.menu_item else None,
            })

        if group_key:
            if group_key in grouped_dine_in:
                existing = grouped_dine_in[group_key]
                existing_items = existing["items"]

                for it in items:
                    match = next((x for x in existing_items if x.get("menu_item_id") == it.get("menu_item_id") and x.get("price") == it.get("price")), None)
                    if match:
                        match["quantity"] = float(match["quantity"] or 0) + float(it["quantity"] or 0)
                    else:
                        existing_items.append(it)

                if not existing["order"].get("bill_no") and order.bill_no:
                    existing["order"]["bill_no"] = order.bill_no
                if not existing["order"].get("dine_in_session_id") and order.dine_in_session_id:
                    existing["order"]["dine_in_session_id"] = order.dine_in_session_id

                curr_status = (existing["order"].get("status") or "").upper()
                new_status = (order.status or "").upper()
                if new_status in ["PREPARING", "READY", "CONFIRMED", "PENDING"]:
                    existing["order"]["status"] = order.status
                elif curr_status not in ["PREPARING", "READY", "CONFIRMED", "PENDING"] and new_status == "SERVED":
                    existing["order"]["status"] = "SERVED"

                if (order.payment_status or "").upper() == "PAID":
                    existing["order"]["payment_status"] = "Paid"
                if order.payment_method and not existing["order"].get("payment_method"):
                    existing["order"]["payment_method"] = order.payment_method

                items_total = round(sum(float(x.get("price") or 0) * float(x.get("quantity") or 1) for x in existing_items), 2)
                existing_total = float(existing["order"].get("total_amount") or 0)
                new_ord_total = float(order.total_amount or 0)
                existing["order"]["total_amount"] = round(max(existing_total, new_ord_total, items_total), 2)
                continue
            else:
                entry = {
                    "order": {
                        "id": order.id,
                        "orderId": f"ORD-{str(order.id).zfill(6)}",
                        "restaurant_id": order.restaurant_id,
                        "table_number": resolve_order_table_number(order, table_number_map),
                        "order_type": getattr(order, "order_type", "DINE_IN"),
                        "bill_no": order.bill_no,
                        "dine_in_session_id": order.dine_in_session_id,
                        "delivery_status": getattr(order, "delivery_status", None),
                        "delivery_address": json.loads(order.delivery_address_snapshot) if isinstance(getattr(order, "delivery_address_snapshot", None), str) else getattr(order, "delivery_address_snapshot", {}),
                        "status": order.status,
                        "payment_method": order.payment_method,
                        "payment_status": order.payment_status,
                        "total_amount": order.total_amount,
                        "created_at": order.created_at,
                    },
                    "items": items,
                }
                if order.dine_in_session_id:
                    sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
                    if sess and float(sess.total_amount or 0) > 0:
                        entry["order"]["total_amount"] = float(sess.total_amount)
                grouped_dine_in[group_key] = entry
                result.append(entry)
        else:
            result.append({
                "order": {
                    "id": order.id,
                    "orderId": f"ORD-{str(order.id).zfill(6)}",
                    "restaurant_id": order.restaurant_id,
                    "table_number": resolve_order_table_number(order, table_number_map),
                    "order_type": getattr(order, "order_type", "DINE_IN"),
                    "bill_no": order.bill_no,
                    "dine_in_session_id": order.dine_in_session_id,
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

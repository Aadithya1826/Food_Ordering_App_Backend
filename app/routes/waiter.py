"""
Waiter authentication and management routes.

New endpoints (backward-compatible — no existing endpoints changed):
  POST /api/v1/auth/waiter/register   — Self-registration (pending approval)
  POST /api/v1/auth/waiter/login      — Phone + password login
  GET  /api/v1/waiters                — List waiters (SUPER_ADMIN, HOTEL_ADMIN)
  PATCH /api/v1/waiters/{id}/approve  — Assign hotel + approve (SUPER_ADMIN, HOTEL_ADMIN)
  PATCH /api/v1/waiters/{id}          — Deactivate/update (SUPER_ADMIN, HOTEL_ADMIN)
  DELETE /api/v1/waiters/{id}         — Remove (SUPER_ADMIN only)

Hotel isolation:
  HOTEL_ADMIN can only approve/list waiters assigned to their own restaurant.
  SUPER_ADMIN can manage waiters across all restaurants.

Token refresh:
  Waiters use the same /api/v1/auth/refresh endpoint as other roles.
"""
import re
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.user import User
from ..models.restaurant import Restaurant
from ..utils.auth import verify_password, create_token, hash_password
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role
from ..schemas import WaiterRegisterRequest, WaiterLoginRequest, WaiterApproveRequest, WaiterResponse

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _normalize_phone(raw: str) -> str:
    """Extract the last 10 digits of a phone number (strips +91, country code, spaces, dashes)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def _build_synthetic_email(phone: str) -> str:
    """Internal email key for waiter records. Never exposed to the user."""
    return f"waiter_{phone}@dataudipi.internal"


def _waiter_to_dict(waiter: User, restaurant: Restaurant | None) -> dict:
    return {
        "id": waiter.id,
        "name": waiter.name,
        "phone": waiter.phone,
        "role": waiter.role,
        "restaurant_id": waiter.restaurant_id,
        "restaurant_name": restaurant.name if restaurant else None,
        "is_active": waiter.is_active,
        "is_approved": waiter.is_approved,
        "created_at": waiter.created_at.isoformat() if waiter.created_at else None,
    }


# ── REGISTRATION ──────────────────────────────────────────────────────────────

@router.post("/api/v1/auth/waiter/register")
def waiter_register(data: WaiterRegisterRequest, db: Session = Depends(get_db)):
    """
    Waiter self-registration.
    - Accepts name, phone, password, confirm_password.
    - Creates account with role=WAITER, restaurant_id=None, is_approved=False.
    - The waiter cannot log in until an admin approves and assigns a hotel.
    - Duplicate phone numbers are rejected with a generic error (no enumeration).
    """
    # Validate passwords match
    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Normalize phone
    phone = _normalize_phone(data.phone)
    if len(phone) != 10:
        raise HTTPException(status_code=400, detail="Please enter a valid 10-digit Indian mobile number")

    name = data.name.strip()
    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Please enter your full name")

    # Check duplicate phone (generic message to avoid enumeration)
    existing = db.query(User).filter(User.phone == phone).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Registration failed. This phone number may already be registered."
        )

    # Synthetic internal email (never shown to user, only used for uniqueness in email col)
    synthetic_email = _build_synthetic_email(phone)
    if db.query(User).filter(User.email == synthetic_email).first():
        raise HTTPException(
            status_code=400,
            detail="Registration failed. This phone number may already be registered."
        )

    hashed = hash_password(data.password)

    new_waiter = User(
        name=name,
        email=synthetic_email,
        phone=phone,
        password_hash=hashed,
        role="WAITER",
        restaurant_id=None,    # assigned by admin after approval
        is_active=True,
        is_approved=False,     # pending — cannot log in yet
    )
    db.add(new_waiter)
    db.commit()
    db.refresh(new_waiter)

    return {
        "status": "pending",
        "message": (
            "Registration successful! Your account is pending approval. "
            "An admin will assign you to a hotel before you can log in."
        ),
        "waiter_id": new_waiter.id,
    }


# ── LOGIN ─────────────────────────────────────────────────────────────────────

@router.post("/api/v1/auth/waiter/login")
def waiter_login(data: WaiterLoginRequest, response: Response, db: Session = Depends(get_db)):
    """
    Waiter login using phone number + password.
    Returns JWT token on success with user info including hotel assignment.
    Rejects: wrong credentials, inactive accounts, unapproved accounts.
    """
    phone = _normalize_phone(data.phone)
    if len(phone) != 10:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    waiter = db.query(User).filter(User.phone == phone, User.role == "WAITER").first()

    # Generic error for wrong phone or wrong password (no enumeration)
    if not waiter or not verify_password(data.password, waiter.password_hash):
        raise HTTPException(status_code=401, detail="Invalid phone number or password")

    if not waiter.is_active:
        raise HTTPException(status_code=401, detail="Your account has been deactivated. Contact admin.")

    # Not yet approved / no hotel assigned
    if not waiter.is_approved or waiter.restaurant_id is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Account pending approval. "
                "An admin needs to assign you to a hotel before you can log in. "
                "Please contact your hotel manager."
            ),
        )

    restaurant = db.query(Restaurant).filter(Restaurant.id == waiter.restaurant_id).first()

    token = create_token(waiter, account_type="STAFF")

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=3600,
        expires=3600,
        secure=False,
        samesite="lax",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": waiter.id,
            "name": waiter.name,
            "phone": waiter.phone,
            "role": waiter.role,
            "restaurant_id": waiter.restaurant_id,
            "restaurant_name": restaurant.name if restaurant else None,
            "is_approved": waiter.is_approved,
        },
    }


# ── LIST WAITERS ──────────────────────────────────────────────────────────────

@router.get("/api/v1/waiters")
def list_waiters(
    restaurant_id: int | None = None,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all waiters.
    - SUPER_ADMIN / ADMIN: sees all waiters, optionally filtered by restaurant_id (always keeping pending waiters).
    - HOTEL_ADMIN: sees only waiters assigned to their restaurant.
    """
    require_role(user, ["SUPER_ADMIN", "ADMIN", "HOTEL_ADMIN"])

    from sqlalchemy import or_

    q = db.query(User).filter(User.role == "WAITER")

    if user.role == "HOTEL_ADMIN":
        q = q.filter(User.restaurant_id == user.restaurant_id)
    elif restaurant_id is not None:
        # If filtered by restaurant, also include unassigned pending waiters so approvals are never hidden
        q = q.filter(
            or_(
                User.restaurant_id == restaurant_id,
                User.restaurant_id.is_(None),
                User.is_approved.is_(False),
                User.is_approved.is_(None),
            )
        )

    waiters = q.order_by(User.created_at.desc()).all()

    rest_cache: dict[int, Restaurant] = {}
    result = []
    for w in waiters:
        if w.restaurant_id and w.restaurant_id not in rest_cache:
            r = db.query(Restaurant).filter(Restaurant.id == w.restaurant_id).first()
            if r:
                rest_cache[w.restaurant_id] = r
        result.append(_waiter_to_dict(w, rest_cache.get(w.restaurant_id)))

    # Also include unassigned pending waiters (visible to SUPER_ADMIN)
    return result


# ── APPROVE & ASSIGN HOTEL ────────────────────────────────────────────────────

@router.patch("/api/v1/waiters/{waiter_id}/approve")
def approve_waiter(
    waiter_id: int,
    data: WaiterApproveRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Approve a pending waiter and assign them to exactly one existing hotel.
    - SUPER_ADMIN / ADMIN: can assign to any restaurant.
    - HOTEL_ADMIN: can only assign to their own restaurant.
    """
    require_role(user, ["SUPER_ADMIN", "ADMIN", "HOTEL_ADMIN"])

    if user.role == "HOTEL_ADMIN" and data.restaurant_id != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail="Hotel admin can only assign waiters to their own restaurant",
        )

    restaurant = db.query(Restaurant).filter(Restaurant.id == data.restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail=f"Restaurant {data.restaurant_id} not found")

    waiter = db.query(User).filter(User.id == waiter_id, User.role == "WAITER").first()
    if not waiter:
        raise HTTPException(status_code=404, detail="Waiter not found")

    waiter.restaurant_id = data.restaurant_id
    waiter.is_approved = True
    waiter.is_active = True
    db.commit()
    db.refresh(waiter)

    return {
        "status": "approved",
        "message": f"Waiter '{waiter.name}' approved and assigned to {restaurant.name}",
        "waiter": _waiter_to_dict(waiter, restaurant),
    }


# ── UPDATE WAITER ─────────────────────────────────────────────────────────────

@router.patch("/api/v1/waiters/{waiter_id}")
def update_waiter(
    waiter_id: int,
    data: dict,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update waiter name, is_active, restaurant_id, or is_approved.
    HOTEL_ADMIN can only modify waiters in their own restaurant.
    """
    require_role(user, ["SUPER_ADMIN", "ADMIN", "HOTEL_ADMIN"])

    waiter = db.query(User).filter(User.id == waiter_id, User.role == "WAITER").first()
    if not waiter:
        raise HTTPException(status_code=404, detail="Waiter not found")

    if user.role == "HOTEL_ADMIN" and waiter.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="Access denied to this waiter")

    allowed = {"name", "is_active", "restaurant_id", "is_approved"}
    for field in allowed:
        if field in data:
            setattr(waiter, field, data[field])

    db.commit()
    db.refresh(waiter)

    restaurant = db.query(Restaurant).filter(Restaurant.id == waiter.restaurant_id).first() if waiter.restaurant_id else None
    return {"status": "success", "waiter": _waiter_to_dict(waiter, restaurant)}


# ── DELETE WAITER ─────────────────────────────────────────────────────────────

@router.delete("/api/v1/waiters/{waiter_id}")
def delete_waiter(
    waiter_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a waiter account. SUPER_ADMIN / ADMIN only."""
    require_role(user, ["SUPER_ADMIN", "ADMIN"])

    waiter = db.query(User).filter(User.id == waiter_id, User.role == "WAITER").first()
    if not waiter:
        raise HTTPException(status_code=404, detail="Waiter not found")

    db.delete(waiter)
    db.commit()
    return {"status": "success", "message": f"Waiter {waiter_id} removed"}

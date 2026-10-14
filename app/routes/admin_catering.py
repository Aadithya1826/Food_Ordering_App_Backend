import datetime
from typing import List, Optional, Union
import uuid

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query, status
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
# pyrefly: ignore [missing-import]
from sqlalchemy import func, desc, or_, and_
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..utils.dependencies import get_db, get_current_user
from ..utils.roles import require_role, resolve_restaurant_id
from ..models.customer import Customer, CustomerAddress
from ..models.menu import CateringOrderMenu
from ..models.catering import (
    CateringSession, CateringSessionCustomization, CateringSessionAddon,
    CateringOrder, CateringOrderCustomization, CateringOrderAddon, CateringPayment
)

router = APIRouter(
    prefix="/api/v1/admin/catering",
    tags=["Admin Catering"]
)

# -------------------------------------------------------------
# SCHEMAS
# -------------------------------------------------------------

class AdminCateringItemInput(BaseModel):
    id: Optional[int] = None
    item_name: str
    category_name: Optional[str] = "General"
    customization_group: Optional[str] = None
    is_swappable: bool = True
    is_removable: bool = True
    add_price: float = 30.0
    remove_price: float = -10.0
    same_group_replace_price: float = 0.0
    upgrade_group: Optional[str] = None
    upgrade_price: float = 0.0
    display_order: int = 0

class AdminCateringPackageCreate(BaseModel):
    name: str
    code: str
    price: float
    minimum_order_quantity: int = 50
    is_available: bool = True
    restaurant_id: Optional[int] = None
    items: List[AdminCateringItemInput] = []

class AdminCateringPackageUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    minimum_order_quantity: Optional[int] = None
    is_available: Optional[bool] = None
    restaurant_id: Optional[int] = None
    items: Optional[List[AdminCateringItemInput]] = None

class AdminCateringAvailabilityToggle(BaseModel):
    is_available: bool

class AdminCateringItemResponse(BaseModel):
    id: int
    item_name: Optional[str]
    category_name: Optional[str]
    customization_group: Optional[str]
    is_swappable: bool
    is_removable: bool
    add_price: float
    remove_price: float
    same_group_replace_price: float
    upgrade_group: Optional[str]
    upgrade_price: float
    display_order: int

    class Config:
        from_attributes = True

class AdminCateringPackageResponse(BaseModel):
    name: str
    code: str
    price: float
    minimum_order_quantity: int
    is_available: bool
    restaurant_id: Optional[int] = None
    items_count: int = 0
    categories_count: int = 0
    items_summary: Optional[str] = None
    dishes_sample: List[str] = []
    items: List[AdminCateringItemResponse] = []

class AdminCateringOrderStatusUpdate(BaseModel):
    order_status: str

# -------------------------------------------------------------
# 1. CATERING PACKAGES CRUD
# -------------------------------------------------------------

@router.get("/packages", response_model=List[AdminCateringPackageResponse])
def get_admin_packages(
    restaurant_id: Optional[int] = None,
    search: Optional[str] = None,
    status_filter: Optional[str] = None, # all, available, drafts
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    List all catering packages grouped from item-level catering_order_menu rows.
    Returns package metadata, item summaries, and full item arrays matching the wireframe.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrderMenu)
    if effective_restaurant_id is not None:
        query = query.filter(
            or_(
                CateringOrderMenu.restaurant_id == effective_restaurant_id,
                CateringOrderMenu.restaurant_id.is_(None)
            )
        )

    all_rows = query.order_by(CateringOrderMenu.code, CateringOrderMenu.display_order, CateringOrderMenu.id).all()

    # Group rows by package code
    packages_map = {}
    for row in all_rows:
        code_key = row.code or row.name
        if code_key not in packages_map:
            packages_map[code_key] = {
                "name": row.name,
                "code": row.code or row.name,
                "price": row.price,
                "minimum_order_quantity": row.minimum_order_quantity or 50,
                "is_available": bool(row.is_available),
                "restaurant_id": row.restaurant_id,
                "items": []
            }
        
        # Only add if it represents an item
        if row.item_name:
            packages_map[code_key]["items"].append(
                AdminCateringItemResponse(
                    id=row.id,
                    item_name=row.item_name,
                    category_name=row.category_name or "General",
                    customization_group=row.customization_group,
                    is_swappable=bool(row.is_swappable),
                    is_removable=bool(row.is_removable),
                    add_price=float(row.add_price or 0.0),
                    remove_price=float(row.remove_price or 0.0),
                    same_group_replace_price=float(row.same_group_replace_price or 0.0),
                    upgrade_group=row.upgrade_group,
                    upgrade_price=float(row.upgrade_price or 0.0),
                    display_order=row.display_order or 0
                )
            )

    result = []
    for code_key, pkg in packages_map.items():
        # Status filter
        if status_filter == "available" and not pkg["is_available"]:
            continue
        if (status_filter in ["drafts", "draft"]) and pkg["is_available"]:
            continue

        item_names = [i.item_name for i in pkg["items"] if i.item_name]
        categories = {i.category_name for i in pkg["items"] if i.category_name}

        # Build human-friendly dishes sample (lines for the wireframe card)
        # Group items into lines e.g. "Idli & Medu Vada · Mini Masala Dosa"
        dishes_sample = []
        for idx in range(0, min(len(item_names), 9), 3):
            chunk = item_names[idx:idx+3]
            dishes_sample.append(" · ".join(chunk))

        summary = None
        if item_names:
            if len(item_names) <= 5:
                summary = ", ".join(item_names[:-1]) + (" & " + item_names[-1] if len(item_names) > 1 else item_names[0])
            else:
                summary = ", ".join(item_names[:4]) + f" & {len(item_names) - 4} more"

        # Search filter
        if search:
            search_lower = search.strip().lower()
            matches_name = search_lower in pkg["name"].lower()
            matches_code = search_lower in pkg["code"].lower()
            matches_items = any(search_lower in (i.item_name or "").lower() for i in pkg["items"])
            if not (matches_name or matches_code or matches_items):
                continue

        result.append(
            AdminCateringPackageResponse(
                name=pkg["name"],
                code=pkg["code"],
                price=pkg["price"],
                minimum_order_quantity=pkg["minimum_order_quantity"],
                is_available=pkg["is_available"],
                restaurant_id=pkg["restaurant_id"],
                items_count=len(pkg["items"]),
                categories_count=len(categories),
                items_summary=summary,
                dishes_sample=dishes_sample,
                items=pkg["items"]
            )
        )

    # Sort packages alphabetically or by code
    result.sort(key=lambda p: p.name)
    return result


@router.get("/packages/{package_code}", response_model=AdminCateringPackageResponse)
def get_admin_package_detail(
    package_code: str,
    restaurant_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Get detailed catering package information and all item customization rules by package code.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == package_code)
    if effective_restaurant_id is not None:
        query = query.filter(
            or_(
                CateringOrderMenu.restaurant_id == effective_restaurant_id,
                CateringOrderMenu.restaurant_id.is_(None)
            )
        )

    rows = query.order_by(CateringOrderMenu.display_order, CateringOrderMenu.id).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Catering package not found")

    header = rows[0]
    items = [
        AdminCateringItemResponse(
            id=r.id,
            item_name=r.item_name,
            category_name=r.category_name or "General",
            customization_group=r.customization_group,
            is_swappable=bool(r.is_swappable),
            is_removable=bool(r.is_removable),
            add_price=float(r.add_price or 0.0),
            remove_price=float(r.remove_price or 0.0),
            same_group_replace_price=float(r.same_group_replace_price or 0.0),
            upgrade_group=r.upgrade_group,
            upgrade_price=float(r.upgrade_price or 0.0),
            display_order=r.display_order or 0
        )
        for r in rows if r.item_name
    ]

    item_names = [i.item_name for i in items if i.item_name]
    dishes_sample = []
    for idx in range(0, min(len(item_names), 9), 3):
        chunk = item_names[idx:idx+3]
        dishes_sample.append(" · ".join(chunk))

    summary = ", ".join(item_names[:4]) + (f" & {len(item_names)-4} more" if len(item_names) > 4 else "")

    return AdminCateringPackageResponse(
        name=header.name,
        code=header.code,
        price=header.price,
        minimum_order_quantity=header.minimum_order_quantity or 50,
        is_available=bool(header.is_available),
        restaurant_id=header.restaurant_id,
        items_count=len(items),
        categories_count=len({i.category_name for i in items if i.category_name}),
        items_summary=summary,
        dishes_sample=dishes_sample,
        items=items
    )


@router.post("/packages", response_model=AdminCateringPackageResponse)
def create_admin_package(
    data: AdminCateringPackageCreate,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Create a new catering package with configured menu items and customization rules.
    Stores records into the existing catering_order_menu table without breaking schema.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, data.restaurant_id)

    # Check if package code already exists for this restaurant
    code_check = db.query(CateringOrderMenu).filter(
        CateringOrderMenu.code == data.code.strip()
    )
    if effective_restaurant_id is not None:
        code_check = code_check.filter(CateringOrderMenu.restaurant_id == effective_restaurant_id)
    
    if code_check.first():
        raise HTTPException(
            status_code=400,
            detail=f"Package code '{data.code}' already exists. Please choose a unique code."
        )

    # If items are provided, create a row for each item
    created_rows = []
    if data.items:
        for idx, item in enumerate(data.items):
            row = CateringOrderMenu(
                name=data.name.strip(),
                code=data.code.strip(),
                price=float(data.price),
                minimum_order_quantity=int(data.minimum_order_quantity),
                is_available=bool(data.is_available),
                restaurant_id=effective_restaurant_id,
                category_name=item.category_name.strip() if item.category_name else "General",
                item_name=item.item_name.strip(),
                customization_group=item.customization_group.strip() if item.customization_group else None,
                is_swappable=bool(item.is_swappable),
                is_removable=bool(item.is_removable),
                add_price=float(item.add_price or 30.0),
                remove_price=float(item.remove_price or -10.0),
                same_group_replace_price=float(item.same_group_replace_price or 0.0),
                upgrade_group=item.upgrade_group.strip() if item.upgrade_group else None,
                upgrade_price=float(item.upgrade_price or 0.0),
                display_order=item.display_order if item.display_order is not None else idx
            )
            db.add(row)
            created_rows.append(row)
    else:
        # Create at least 1 master header row
        row = CateringOrderMenu(
            name=data.name.strip(),
            code=data.code.strip(),
            price=float(data.price),
            minimum_order_quantity=int(data.minimum_order_quantity),
            is_available=bool(data.is_available),
            restaurant_id=effective_restaurant_id,
            display_order=0
        )
        db.add(row)
        created_rows.append(row)

    db.commit()

    return get_admin_package_detail(package_code=data.code.strip(), restaurant_id=effective_restaurant_id, db=db, user=user)


@router.put("/packages/{package_code}", response_model=AdminCateringPackageResponse)
def update_admin_package(
    package_code: str,
    data: AdminCateringPackageUpdate,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Update an existing catering package header and its menu items/customization rules.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, data.restaurant_id)

    query = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == package_code)
    if effective_restaurant_id is not None:
        query = query.filter(
            or_(
                CateringOrderMenu.restaurant_id == effective_restaurant_id,
                CateringOrderMenu.restaurant_id.is_(None)
            )
        )

    rows = query.all()
    if not rows:
        raise HTTPException(status_code=404, detail="Catering package not found")

    # Update common header fields on all rows for this package
    for r in rows:
        if data.name is not None:
            r.name = data.name.strip()
        if data.price is not None:
            r.price = float(data.price)
        if data.minimum_order_quantity is not None:
            r.minimum_order_quantity = int(data.minimum_order_quantity)
        if data.is_available is not None:
            r.is_available = bool(data.is_available)

    # If items array is explicitly provided, synchronize items
    if data.items is not None:
        existing_by_id = {r.id: r for r in rows if r.item_name}
        provided_ids = set()

        for idx, item_input in enumerate(data.items):
            if item_input.id and item_input.id in existing_by_id:
                # Update existing row
                r = existing_by_id[item_input.id]
                provided_ids.add(r.id)
                r.item_name = item_input.item_name.strip()
                r.category_name = item_input.category_name.strip() if item_input.category_name else "General"
                r.customization_group = item_input.customization_group.strip() if item_input.customization_group else None
                r.is_swappable = bool(item_input.is_swappable)
                r.is_removable = bool(item_input.is_removable)
                r.add_price = float(item_input.add_price or 0.0)
                r.remove_price = float(item_input.remove_price or 0.0)
                r.same_group_replace_price = float(item_input.same_group_replace_price or 0.0)
                r.upgrade_group = item_input.upgrade_group.strip() if item_input.upgrade_group else None
                r.upgrade_price = float(item_input.upgrade_price or 0.0)
                r.display_order = item_input.display_order if item_input.display_order is not None else idx
            else:
                # Insert new item row
                header_row = rows[0]
                new_row = CateringOrderMenu(
                    name=header_row.name,
                    code=package_code,
                    price=header_row.price,
                    minimum_order_quantity=header_row.minimum_order_quantity,
                    is_available=header_row.is_available,
                    restaurant_id=header_row.restaurant_id,
                    category_name=item_input.category_name.strip() if item_input.category_name else "General",
                    item_name=item_input.item_name.strip(),
                    customization_group=item_input.customization_group.strip() if item_input.customization_group else None,
                    is_swappable=bool(item_input.is_swappable),
                    is_removable=bool(item_input.is_removable),
                    add_price=float(item_input.add_price or 30.0),
                    remove_price=float(item_input.remove_price or -10.0),
                    same_group_replace_price=float(item_input.same_group_replace_price or 0.0),
                    upgrade_group=item_input.upgrade_group.strip() if item_input.upgrade_group else None,
                    upgrade_price=float(item_input.upgrade_price or 0.0),
                    display_order=item_input.display_order if item_input.display_order is not None else idx
                )
                db.add(new_row)

        # Delete items that were omitted
        for item_id, r in existing_by_id.items():
            if item_id not in provided_ids:
                db.delete(r)

    db.commit()

    return get_admin_package_detail(package_code=package_code, restaurant_id=effective_restaurant_id, db=db, user=user)


@router.patch("/packages/{package_code}/availability")
def toggle_admin_package_availability(
    package_code: str,
    payload: AdminCateringAvailabilityToggle,
    restaurant_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Toggle package availability (Available / Draft) across all rows for this package.
    Immediately synchronizes with customer package listing.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == package_code)
    if effective_restaurant_id is not None:
        query = query.filter(
            or_(
                CateringOrderMenu.restaurant_id == effective_restaurant_id,
                CateringOrderMenu.restaurant_id.is_(None)
            )
        )

    rows = query.all()
    if not rows:
        raise HTTPException(status_code=404, detail="Catering package not found")

    for r in rows:
        r.is_available = payload.is_available

    db.commit()
    return {"message": "Package availability updated", "code": package_code, "is_available": payload.is_available}


@router.delete("/packages/{package_code}")
def delete_admin_package(
    package_code: str,
    restaurant_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Safe deletion/deactivation of a catering package.
    If historical confirmed orders exist for this package, performs safe deactivation (soft delete: is_available=False)
    to preserve historical order integrity. If no orders exist, cleans up master rows.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == package_code)
    if effective_restaurant_id is not None:
        query = query.filter(
            or_(
                CateringOrderMenu.restaurant_id == effective_restaurant_id,
                CateringOrderMenu.restaurant_id.is_(None)
            )
        )

    rows = query.all()
    if not rows:
        raise HTTPException(status_code=404, detail="Catering package not found")

    # Check for existing catering orders referencing this package code
    existing_orders_count = db.query(CateringOrder).filter(CateringOrder.package_code == package_code).count()

    if existing_orders_count > 0:
        # Safe deactivation (soft delete) to protect order history integrity
        for r in rows:
            r.is_available = False
        db.commit()
        return {
            "message": f"Package deactivated safely. Retained historical references for {existing_orders_count} confirmed order(s).",
            "action": "DEACTIVATED",
            "code": package_code
        }
    else:
        for r in rows:
            db.delete(r)
        db.commit()
        return {
            "message": "Package deleted successfully.",
            "action": "DELETED",
            "code": package_code
        }


# -------------------------------------------------------------
# 2. CATERING ORDERS MANAGEMENT
# -------------------------------------------------------------

@router.get("/orders")
def get_admin_catering_orders(
    restaurant_id: Optional[int] = None,
    order_status: Optional[str] = None,
    payment_status: Optional[str] = None,
    package_code: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[datetime.date] = None,
    date_to: Optional[datetime.date] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    List real confirmed catering orders with deep filtering:
    - Order status (CONFIRMED, PREPARING, READY, COMPLETED, CANCELLED)
    - Payment status (PENDING, PARTIALLY_PAID, PAID)
    - Date filters
    - Customer and package search
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrder, Customer).join(
        Customer, CateringOrder.customer_id == Customer.id, isouter=True
    ).options(
        joinedload(CateringOrder.payments),
        joinedload(CateringOrder.customizations),
        joinedload(CateringOrder.addons)
    )

    if effective_restaurant_id is not None:
        query = query.filter(CateringOrder.restaurant_id == effective_restaurant_id)

    if order_status:
        query = query.filter(func.upper(CateringOrder.order_status) == order_status.upper())

    if payment_status:
        query = query.filter(func.upper(CateringOrder.payment_status) == payment_status.upper())

    if package_code:
        query = query.filter(CateringOrder.package_code == package_code)

    if date_from:
        start_dt = datetime.datetime.combine(date_from, datetime.time.min)
        query = query.filter(CateringOrder.event_date >= start_dt)

    if date_to:
        end_dt = datetime.datetime.combine(date_to, datetime.time.max)
        query = query.filter(CateringOrder.event_date <= end_dt)

    orders_with_customers = query.order_by(desc(CateringOrder.created_at)).all()

    result = []
    for order, customer in orders_with_customers:
        cust_name = customer.name if customer else "Guest"
        cust_phone = customer.phone if customer else "N/A"
        cust_email = customer.email if customer else ""

        # Full-text search
        if search:
            s = search.strip().lower()
            matches = (
                s in str(order.id) or
                s in (cust_name or "").lower() or
                s in (cust_phone or "").lower() or
                s in (order.package_name or "").lower() or
                s in (order.package_code or "").lower() or
                s in (order.event_name or "").lower()
            )
            if not matches:
                continue

        # Verified payments calculation
        verified_payments = [p for p in (order.payments or []) if p.payment_status == "SUCCESS"]
        verified_paid = sum(p.amount for p in verified_payments)

        # Real outstanding balance
        balance = max(0.0, float(order.total_amount or 0.0) - verified_paid)

        result.append({
            "id": order.id,
            "catering_session_id": str(order.catering_session_id),
            "customer_id": order.customer_id,
            "customer_name": cust_name,
            "customer_phone": cust_phone,
            "customer_email": cust_email,
            "restaurant_id": order.restaurant_id,
            "package_name": order.package_name,
            "package_code": order.package_code,
            "package_price": order.package_price,
            "event_name": order.event_name,
            "event_date": order.event_date.isoformat() if order.event_date else None,
            "serving_time": order.serving_time,
            "occasion": order.occasion,
            "guest_count": order.guest_count,
            "service_type": order.service_type,
            "delivery_address_snapshot": order.delivery_address_snapshot,
            "total_amount": float(order.total_amount or 0.0),
            "advance_amount": float(order.advance_amount or 0.0),
            "paid_amount": verified_paid,
            "balance_amount": balance,
            "payment_status": order.payment_status or ("PAID" if balance == 0 else "PARTIALLY_PAID" if verified_paid > 0 else "PENDING"),
            "order_status": order.order_status or "CONFIRMED",
            "full_payment_due_date": order.full_payment_due_date.isoformat() if order.full_payment_due_date else None,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "customizations_count": len(order.customizations or []),
            "addons_count": len(order.addons or []),
            "payments_count": len(verified_payments)
        })

    return result


@router.get("/orders/{order_id}")
def get_admin_catering_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Get deep, verified details of a confirmed catering order:
    - Customer Info
    - Event Info
    - Package Details & Booked Price
    - Complete Customization History (Added, Removed, Replaced)
    - Addon Services
    - Verified Transaction History (Advance, Balance)
    - Payment Due Date & Outstanding Balance
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    order = db.query(CateringOrder).options(
        joinedload(CateringOrder.payments),
        joinedload(CateringOrder.customizations),
        joinedload(CateringOrder.addons)
    ).filter(CateringOrder.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Catering order not found")

    customer = db.query(Customer).filter(Customer.id == order.customer_id).first()

    # Verified payments
    verified_payments = [p for p in (order.payments or []) if p.payment_status == "SUCCESS"]
    verified_paid = sum(p.amount for p in verified_payments)
    balance = max(0.0, float(order.total_amount or 0.0) - verified_paid)

    # Customization breakdown
    customizations_data = []
    for c in (order.customizations or []):
        customizations_data.append({
            "id": c.id,
            "action_type": c.action_type,
            "original_item_name": c.original_item_name,
            "original_category": c.original_category,
            "original_group": c.original_group,
            "new_item_name": c.new_item_name,
            "new_category": c.new_category,
            "new_group": c.new_group,
            "price_adjustment_per_person": float(c.price_adjustment_per_person or 0.0),
            "guest_count": c.guest_count or order.guest_count,
            "total_price_adjustment": float(c.total_price_adjustment or 0.0)
        })

    # Addons breakdown
    addons_data = []
    for a in (order.addons or []):
        addons_data.append({
            "id": a.id,
            "addon_type": a.addon_type,
            "addon_name": a.addon_name,
            "quantity": a.quantity,
            "unit_price": float(a.unit_price or 0.0),
            "pricing_type": a.pricing_type,
            "total_price": float(a.total_price or 0.0)
        })

    # Payments list
    payments_data = []
    for p in (order.payments or []):
        payments_data.append({
            "id": p.id,
            "transaction_id": p.transaction_id,
            "amount": float(p.amount or 0.0),
            "payment_type": p.payment_type, # ADVANCE or BALANCE
            "payment_method": p.payment_method or "Razorpay",
            "payment_status": p.payment_status,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })

    # Included dishes from package snapshot / current package items
    package_items = db.query(CateringOrderMenu.item_name, CateringOrderMenu.category_name).filter(
        CateringOrderMenu.code == order.package_code
    ).order_by(CateringOrderMenu.display_order).all()

    included_dishes = [
        {"item_name": r.item_name, "category_name": r.category_name or "General"}
        for r in package_items if r.item_name
    ]

    return {
        "order": {
            "id": order.id,
            "catering_session_id": str(order.catering_session_id),
            "restaurant_id": order.restaurant_id,
            "order_status": order.order_status or "CONFIRMED",
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None
        },
        "customer": {
            "id": customer.id if customer else order.customer_id,
            "name": customer.name if customer else "Guest",
            "phone": customer.phone if customer else "N/A",
            "email": customer.email if customer else "",
            "address": customer.address if customer else ""
        },
        "event": {
            "event_name": order.event_name,
            "event_date": order.event_date.isoformat() if order.event_date else None,
            "serving_time": order.serving_time,
            "occasion": order.occasion,
            "guest_count": order.guest_count,
            "service_type": order.service_type,
            "delivery_address_snapshot": order.delivery_address_snapshot,
            "dietary_notes": order.dietary_notes,
            "spice_level": order.spice_level
        },
        "package": {
            "package_name": order.package_name,
            "package_code": order.package_code,
            "booked_package_price": float(order.package_price or 0.0),
            "included_dishes": included_dishes
        },
        "financials": {
            "base_amount": float(order.base_amount or 0.0),
            "customization_amount": float(order.customization_amount or 0.0),
            "addon_amount": float(order.addon_amount or 0.0),
            "transport_charge": float(order.transport_charge or 0.0),
            "service_charge": float(order.service_charge or 0.0),
            "cgst_amount": float(order.cgst_amount or 0.0),
            "sgst_amount": float(order.sgst_amount or 0.0),
            "total_amount": float(order.total_amount or 0.0),
            "advance_percentage": float(order.advance_percentage or 50.0),
            "advance_amount": float(order.advance_amount or 0.0),
            "paid_amount": verified_paid,
            "balance_amount": balance,
            "payment_status": order.payment_status or ("PAID" if balance == 0 else "PARTIALLY_PAID" if verified_paid > 0 else "PENDING"),
            "full_payment_due_date": order.full_payment_due_date.isoformat() if order.full_payment_due_date else None
        },
        "customizations": customizations_data,
        "addons": addons_data,
        "payments": payments_data
    }


@router.patch("/orders/{order_id}/status")
def update_admin_catering_order_status(
    order_id: int,
    payload: AdminCateringOrderStatusUpdate,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Update status of confirmed catering order:
    Transitions: CONFIRMED -> PREPARING -> READY -> COMPLETED | CANCELLED
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    order = db.query(CateringOrder).filter(CateringOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Catering order not found")

    new_status = payload.order_status.strip().upper()
    valid_statuses = ["CONFIRMED", "PREPARING", "READY", "COMPLETED", "CANCELLED"]

    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{payload.order_status}'. Allowed: {', '.join(valid_statuses)}"
        )

    current_status = (order.order_status or "CONFIRMED").upper()

    # Business transition rules
    if current_status == "COMPLETED" and new_status != "COMPLETED":
        raise HTTPException(status_code=400, detail="Cannot alter an already completed catering service.")
    if current_status == "CANCELLED" and new_status != "CANCELLED":
        raise HTTPException(status_code=400, detail="Cannot reactivate a cancelled catering order.")

    order.order_status = new_status
    db.commit()

    return {
        "message": f"Order status updated to {new_status}",
        "order_id": order.id,
        "order_status": new_status
    }


# -------------------------------------------------------------
# 3. CATERING REPORTS & METRICS
# -------------------------------------------------------------

@router.get("/reports")
def get_admin_catering_reports(
    restaurant_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Catering reporting architecture:
    - Total confirmed catering orders
    - Booked order value (catering sales)
    - Verified advance collections
    - Verified balance collections
    - Total collected revenue
    - Outstanding receivables
    - Upcoming events
    - Completed events
    Distinguishes booked commitments from actual collected bank revenue without double counting.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    effective_restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(CateringOrder).options(joinedload(CateringOrder.payments))
    if effective_restaurant_id is not None:
        query = query.filter(CateringOrder.restaurant_id == effective_restaurant_id)

    if start_date:
        start_dt = datetime.datetime.combine(start_date, datetime.time.min)
        query = query.filter(CateringOrder.created_at >= start_dt)

    if end_date:
        end_dt = datetime.datetime.combine(end_date, datetime.time.max)
        query = query.filter(CateringOrder.created_at <= end_dt)

    orders = query.all()

    total_orders = len(orders)
    active_orders = [o for o in orders if (o.order_status or "").upper() != "CANCELLED"]

    # Booked order value (commitments)
    total_booked_sales = sum(float(o.total_amount or 0.0) for o in active_orders)

    # Collections from verified payments
    verified_advances = 0.0
    verified_balances = 0.0

    for o in active_orders:
        for p in (o.payments or []):
            if p.payment_status == "SUCCESS":
                if p.payment_type == "ADVANCE":
                    verified_advances += float(p.amount or 0.0)
                elif p.payment_type == "BALANCE":
                    verified_balances += float(p.amount or 0.0)

    total_collected_revenue = verified_advances + verified_balances
    outstanding_receivables = max(0.0, total_booked_sales - total_collected_revenue)

    # Event tracking
    today_date = datetime.datetime.utcnow().date()
    upcoming_events = 0
    for o in active_orders:
        if o.event_date:
            ev_date = o.event_date.date() if isinstance(o.event_date, datetime.datetime) else o.event_date
            if ev_date >= today_date:
                upcoming_events += 1
    completed_events = sum(1 for o in orders if (o.order_status or "").upper() == "COMPLETED")

    # Status distribution
    status_distribution = {}
    for o in orders:
        st = (o.order_status or "CONFIRMED").upper()
        status_distribution[st] = status_distribution.get(st, 0) + 1

    # Package distribution
    package_distribution = {}
    for o in active_orders:
        pkg = o.package_name or "Unknown"
        package_distribution[pkg] = package_distribution.get(pkg, 0) + 1

    return {
        "total_orders": total_orders,
        "total_booked_sales": total_booked_sales,
        "verified_advance_collections": verified_advances,
        "verified_balance_collections": verified_balances,
        "total_collected_revenue": total_collected_revenue,
        "outstanding_receivables": outstanding_receivables,
        "upcoming_events": upcoming_events,
        "completed_events": completed_events,
        "status_distribution": status_distribution,
        "package_distribution": package_distribution
    }

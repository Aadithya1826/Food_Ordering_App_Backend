# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request, Header
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
# pyrefly: ignore [missing-import]
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Any, Union
from pydantic import BaseModel
from ..db import SessionLocal
from ..models.order import Order, OrderItem
from ..schemas.order import OrderStatusUpdate, OrderPaymentStatusUpdate
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id, require_restaurant_access
from ..utils.table_refs import build_table_number_map, resolve_order_table_number

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
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN", "WAITER", "CASHIER"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    cutoff = datetime.utcnow() - timedelta(hours=12)
    # Strictly isolate DINE_IN:
    # Takeaway and Delivery are filtered when status == 'SERVED' (unchanged).
    # Active unpaid DINE_IN orders remain visible if they are not SERVED OR if they have newly appended pending kitchen items.
    query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    ).filter(
        Order.status != "CANCELLED",
        Order.created_at >= cutoff,
        or_(
            Order.status != "SERVED",
            (
                (Order.order_type == "DINE_IN") &
                (Order.payment_status != "Paid") &
                Order.items.any(OrderItem.status == "PENDING")
            )
        )
    )
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    orders = query.order_by(Order.id.desc()).limit(100).all()
    table_number_map = build_table_number_map(db, orders)
    response = []


    # Batch pre-fetch customers and users to eliminate N+1 query latency
    phones = [o.customer_phone for o in orders if o.customer_phone and not getattr(o, 'customer_name', None)]
    customer_map = {}
    if phones:
        from ..models.customer import Customer
        cust_records = db.query(Customer).filter(Customer.phone.in_(set(phones))).all()
        customer_map = {c.phone: c.name for c in cust_records if c.name}

    user_ids = [o.user_id for o in orders if o.user_id]
    user_map = {}
    if user_ids:
        from ..models.user import User
        users = db.query(User).filter(User.id.in_(set(user_ids))).all()
        user_map = {u.id: u.name for u in users}

    # Pre-calculate max bill number once if needed
    needs_bill_no = any(not o.bill_no for o in orders)
    current_max_bill = 0
    if needs_bill_no:
        from sqlalchemy import func
        current_max_bill = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == restaurant_id).scalar() or 0

    has_db_changes = False

    for o in orders:
        items = [
            {
                "id": i.id,
                "name": i.menu_item.name if i.menu_item else "Unknown Item",
                "quantity": i.quantity,
                "price": i.price,
                "status": getattr(i, "status", None) or "PENDING"
            }
            for i in o.items
        ]

        # Use defaults if not set
        method = o.payment_method or "Cash"
        # If SERVED, it's typically paid. If PENDING, maybe pending.
        p_status = o.payment_status or ("Paid" if o.status in ["SERVED", "COMPLETED"] else "Pending")

        # Resolve customer name and phone
        customer_name = getattr(o, 'customer_name', None)
        customer_phone = getattr(o, 'customer_phone', None) or o.customer_phone or ""
        if not customer_name and customer_phone:
            customer_name = customer_map.get(customer_phone)
        if not customer_name:
            customer_name = o.customer_phone or "Guest"

        # Fallback to delivery_address_snapshot
        if (not customer_name or customer_name in ["Guest", customer_phone]) and o.delivery_address_snapshot:
            try:
                import json
                raw_addr = json.loads(o.delivery_address_snapshot) if isinstance(o.delivery_address_snapshot, str) else o.delivery_address_snapshot
                if isinstance(raw_addr, dict):
                    addr_name = raw_addr.get("contact_name") or raw_addr.get("name")
                    if addr_name:
                        customer_name = addr_name
                    if not customer_phone:
                        customer_phone = raw_addr.get("contact_phone") or raw_addr.get("phone") or ""
            except Exception:
                pass

        # Resolve placed_by (cashier/waiter who created the order)
        placed_by_name = user_map.get(o.user_id) if o.user_id else None
        placed_by_user_id = o.user_id

        # Ensure bill_no is always populated for KOT verification
        if not o.bill_no:
            current_max_bill += 1
            o.bill_no = current_max_bill
            has_db_changes = True

        response.append({
            "order_id": o.id,
            "restaurant_id": o.restaurant_id,
            "branch_order_id": o.branch_order_id,
            "bill_no": o.bill_no,
            "table_number": resolve_order_table_number(o, table_number_map),
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items,
            "order_type": o.order_type,
            "customer_phone": customer_phone or o.customer_phone,
            "customer_name": customer_name,
            "delivery_address": o.delivery_address_snapshot,
            "dine_in_session_id": o.dine_in_session_id,
            "order_source": getattr(o, "order_source", "CUSTOMER_APP") or "CUSTOMER_APP",
            "placed_by_user_id": placed_by_user_id,
            "placed_by_name": placed_by_name,
            "client_order_id": getattr(o, "client_order_id", None),
            "idempotency_key": getattr(o, "idempotency_key", None),
        })

    if has_db_changes:
        db.commit()

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
    
    # Sync delivery assignment & status if this is a delivery order
    from ..services.delivery_status import sync_order_delivery_on_status_change
    sync_order_delivery_on_status_change(db, order, data.status)

    # Auto-release table if order is completed or cancelled (ONLY if not part of an active Dine-In session)
    if data.status in ["COMPLETED", "CANCELLED"] and order.table_id:
        from ..models.table import Table
        from ..models.dine_in_session import DineInSession
        session_is_active = False
        if order.dine_in_session_id:
            sess = db.query(DineInSession).filter(DineInSession.id == order.dine_in_session_id).first()
            if sess and sess.status == "ACTIVE":
                session_is_active = True
                
        if not session_is_active:
            table = db.query(Table).filter(Table.id == order.table_id).first()
            if table:
                table.status = "Vacant"
            
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
    
    # Optional: if paid, sometimes we also consider it completed/released
    if data.payment_status.lower() == "paid" and order.status == "COMPLETED" and order.table_id:
        from ..models.table import Table
        table = db.query(Table).filter(Table.id == order.table_id).first()
        if table:
            table.status = "Vacant"

    db.commit()

    return {
        "order_id": order.id,
        "payment_status": order.payment_status
    }


# GET all orders (for payments and history)
@router.get("/api/v1/orders", response_model=list[dict])
def get_all_orders(
    restaurant_id: int | None = None,
    date: str | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN", "CASHIER"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.menu_item)
    )
    if restaurant_id is not None:
        query = query.filter(Order.restaurant_id == restaurant_id)

    if user.role == "CASHIER":
        query = query.filter(Order.user_id == user.id)

    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = datetime.utcnow().date()
            
        start_of_day = datetime(target_date.year, target_date.month, target_date.day)
        end_of_day = start_of_day + timedelta(days=1)
        
        # Adjust for IST (UTC+5:30)
        utc_start = start_of_day - timedelta(hours=5, minutes=30)
        utc_end = end_of_day - timedelta(hours=5, minutes=30)
        
        query = query.filter(Order.created_at >= utc_start, Order.created_at < utc_end)

    # For payment dashboard, ordering by latest first
    orders = query.order_by(Order.created_at.desc()).all()
    table_number_map = build_table_number_map(db, orders)

    response = []
    for o in orders:
        items = [
            {
                "id": i.id,
                "name": i.menu_item.name if i.menu_item else "Unknown Item",
                "quantity": i.quantity,
                "price": i.price,
                "status": getattr(i, "status", None) or "PENDING"
            }
            for i in o.items
        ]

        # Use defaults if not set
        method = o.payment_method or "Cash"
        # If SERVED, it's typically paid. If PENDING, maybe pending.
        p_status = o.payment_status or ("Paid" if o.status in ["SERVED", "COMPLETED"] else "Pending")

        # Resolve customer name and phone
        customer_name = getattr(o, 'customer_name', None)
        customer_phone = getattr(o, 'customer_phone', None) or o.customer_phone or ""
        if not customer_name and o.customer_phone:
            from ..models.customer import Customer
            cust = db.query(Customer).filter(Customer.phone == o.customer_phone).first()
            if cust and cust.name:
                customer_name = cust.name
        if not customer_name:
            customer_name = o.customer_phone or "Guest"

        # Fallback to delivery_address_snapshot
        if (not customer_name or customer_name in ["Guest", customer_phone]) and o.delivery_address_snapshot:
            try:
                import json
                raw_addr = json.loads(o.delivery_address_snapshot) if isinstance(o.delivery_address_snapshot, str) else o.delivery_address_snapshot
                if isinstance(raw_addr, dict):
                    addr_name = raw_addr.get("contact_name") or raw_addr.get("name")
                    if addr_name:
                        customer_name = addr_name
                    if not customer_phone:
                        customer_phone = raw_addr.get("contact_phone") or raw_addr.get("phone") or ""
            except Exception:
                pass

        response.append({
            "order_id": o.id,
            "branch_order_id": o.branch_order_id,
            "bill_no": o.bill_no,
            "table_number": resolve_order_table_number(o, table_number_map),
            "status": o.status,
            "payment_method": method,
            "payment_status": p_status,
            "total_amount": o.total_amount,
            "created_at": o.created_at,
            "items": items,
            "order_type": o.order_type,
            "customer_phone": customer_phone or o.customer_phone,
            "customer_name": customer_name,
            "delivery_address": o.delivery_address_snapshot,
            "dine_in_session_id": o.dine_in_session_id,
            "order_source": getattr(o, "order_source", "CUSTOMER_APP") or "CUSTOMER_APP",
            "client_order_id": getattr(o, "client_order_id", None),
            "idempotency_key": getattr(o, "idempotency_key", None),
        })

    return response
class PosCartItem(BaseModel):
    model_config = {"extra": "ignore"}
    id: int
    quantity: int
    price: float

class PosOrderPayload(BaseModel):
    model_config = {"extra": "ignore"}
    table_number: str = "takeaway"
    order_type: Optional[str] = "DINE_IN"
    payment_method: str = "Cash"
    cart: List[PosCartItem] = []
    total_amount: float = 0
    bill_no: Optional[int] = None
    timestamp: Optional[Any] = None
    original_timestamp: Optional[Any] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    client_order_id: Optional[str] = None
    idempotency_key: Optional[str] = None

def parse_business_timestamp(raw_val: Any) -> Optional[datetime]:
    """Parse business timestamp from ISO string, epoch ms, epoch s, or common date formats into UTC datetime."""
    if not raw_val:
        return None
    if isinstance(raw_val, datetime):
        if raw_val.tzinfo is not None:
            return raw_val.astimezone(timezone.utc).replace(tzinfo=None)
        return raw_val
    if isinstance(raw_val, (int, float)):
        try:
            # Epoch milliseconds if > 1e11, otherwise epoch seconds
            if raw_val > 1e11:
                return datetime.fromtimestamp(raw_val / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            return datetime.fromtimestamp(raw_val, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None
    if isinstance(raw_val, str):
        val = raw_val.strip()
        if not val:
            return None
        # Try numeric string (epoch ms or s)
        try:
            num = float(val)
            if num > 1e11:
                return datetime.fromtimestamp(num / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            return datetime.fromtimestamp(num, tz=timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
        # Try ISO 8601 format
        try:
            ts = val.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
        # Try common datetime formats
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d"
        ):
            try:
                return datetime.strptime(val, fmt)
            except Exception:
                continue
    return None

from ..models.table import Table

@router.post("/api/v1/orders", response_model=dict)
def create_pos_order(
    payload: PosOrderPayload,
    request: Request,
    restaurant_id: int | None = None,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    x_original_timestamp: Optional[str] = Header(None, alias="X-Original-Timestamp"),
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN", "CASHIER", "WAITER"])
        res_id = resolve_restaurant_id(user, restaurant_id)
        if not res_id:
            raise HTTPException(status_code=400, detail="restaurant_id is required")

        # 1. Resolve Idempotency Keys (Header has priority, fallback to payload body)
        keys_to_check = set()
        if idempotency_key and str(idempotency_key).strip():
            keys_to_check.add(str(idempotency_key).strip())
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
        if idempotency_key and str(idempotency_key).strip():
            primary_idempotency_key = str(idempotency_key).strip()
        elif payload.client_order_id and str(payload.client_order_id).strip():
            primary_idempotency_key = str(payload.client_order_id).strip()
        elif keys_to_check:
            primary_idempotency_key = next(iter(keys_to_check))

        # 2. Check if key already exists (Idempotency Guarantee)
        # If exists, return existing order with 200 — do not create a second row or duplicate items.
        if keys_to_check:
            existing_order = db.query(Order).options(
                joinedload(Order.items)
            ).filter(
                or_(
                    Order.idempotency_key.in_(keys_to_check),
                    Order.client_order_id.in_(keys_to_check)
                )
            ).first()

            if existing_order:
                table_number = None
                if existing_order.table_id:
                    t = db.query(Table).filter(Table.id == existing_order.table_id).first()
                    if t:
                        table_number = t.table_number

                return {
                    "orderId": existing_order.id,
                    "id": existing_order.id,
                    "branch_order_id": existing_order.branch_order_id,
                    "bill_no": existing_order.bill_no,
                    "status": existing_order.status,
                    "payment_status": existing_order.payment_status,
                    "payment_method": existing_order.payment_method,
                    "order_type": existing_order.order_type,
                    "total_amount": existing_order.total_amount,
                    "table_number": table_number,
                    "created_at": existing_order.created_at.isoformat() if existing_order.created_at else None,
                    "idempotency_key": existing_order.idempotency_key or primary_idempotency_key,
                    "client_order_id": existing_order.client_order_id or primary_idempotency_key,
                    "message": "Order already processed (idempotent replay)",
                    "idempotent": True,
                    "is_duplicate": True
                }

        # 3. Resolve business timestamp (X-Original-Timestamp or body timestamp)
        # Ensures bills entered during an outage land on the correct date in reports instead of sync time.
        raw_timestamp = (
            x_original_timestamp or
            (request.headers.get("X-Original-Timestamp") if request else None) or
            (request.headers.get("x-original-timestamp") if request else None) or
            (request.headers.get("X-Timestamp") if request else None) or
            (request.headers.get("x-timestamp") if request else None) or
            payload.timestamp or
            payload.original_timestamp
        )
        business_time = parse_business_timestamp(raw_timestamp)

        is_takeaway = (
            not payload.table_number or 
            payload.table_number.lower().replace(" ", "").replace("-", "") == "takeaway" or
            (payload.order_type and payload.order_type.upper() == "TAKEAWAY")
        )
        if is_takeaway:
            table_id = None
        else:
            base_num = payload.table_number.replace("T-", "").replace("t-", "").strip()
            table = db.query(Table).filter(
                (Table.table_number == payload.table_number) |
                (Table.table_number == base_num) |
                (Table.table_number == f"T-{base_num}"),
                Table.restaurant_id == res_id
            ).first()
            if not table:
                table = Table(table_number=payload.table_number, restaurant_id=res_id, capacity=4, status="Occupied")
                db.add(table)
                db.commit()
                db.refresh(table)
            table_id = table.id

        # All newly placed POS orders must enter KDS under "New Orders" as PENDING so kitchen prepares them
        status = "PENDING"
        payment_status = "Paid" if payload.payment_method.lower() in ["cash", "upi", "card"] else "Pending"

        # Calculate continuous branch_order_id
        last_order = db.query(Order).filter(Order.restaurant_id == res_id).order_by(Order.branch_order_id.desc().nullslast()).first()
        next_branch_order_id = (last_order.branch_order_id + 1) if last_order and last_order.branch_order_id else 1

        # Check if table has an active Dine-In session to link POS orders to the shared session
        pos_order_type = payload.order_type.upper() if payload.order_type else "DINE_IN"
        dine_in_sess_id = None
        if pos_order_type == "DINE_IN" and table_id:
            from ..models.dine_in_session import DineInSession
            active_sess = db.query(DineInSession).filter(
                DineInSession.restaurant_id == res_id,
                DineInSession.table_id == table_id,
                DineInSession.status == "ACTIVE"
            ).first()
            if active_sess:
                dine_in_sess_id = active_sess.id
                active_sess.total_amount = float(active_sess.total_amount or 0.0) + float(payload.total_amount or 0.0)
                active_sess.updated_at = datetime.utcnow()

        cust_name_clean = payload.customer_name.strip() if payload.customer_name and payload.customer_name.strip() else None
        cust_phone_clean = payload.customer_phone.strip() if payload.customer_phone and payload.customer_phone.strip() else None

        assigned_bill_no = payload.bill_no
        if not assigned_bill_no:
            if dine_in_sess_id:
                sibling_bill = db.query(Order.bill_no).filter(
                    Order.dine_in_session_id == dine_in_sess_id,
                    Order.status != "CANCELLED",
                    Order.bill_no.isnot(None)
                ).first()
                if sibling_bill and sibling_bill[0]:
                    assigned_bill_no = sibling_bill[0]
            if not assigned_bill_no:
                max_bill_no = db.query(func.max(Order.bill_no)).filter(Order.restaurant_id == res_id).scalar() or 0
                assigned_bill_no = (max_bill_no or 0) + 1

        new_order = Order(
            restaurant_id=res_id,
            table_id=table_id,
            dine_in_session_id=dine_in_sess_id,
            order_source="CASHIER_POS",
            user_id=user.id,
            total_amount=payload.total_amount,
            status=status,
            payment_status=payment_status,
            payment_method=payload.payment_method,
            order_type=pos_order_type,
            bill_no=assigned_bill_no,
            branch_order_id=next_branch_order_id,
            customer_name=cust_name_clean,
            customer_phone=cust_phone_clean,
            idempotency_key=primary_idempotency_key,
            client_order_id=payload.client_order_id or primary_idempotency_key,
            created_at=business_time if business_time else datetime.utcnow()
        )
        
        try:
            db.add(new_order)
            db.commit()
            db.refresh(new_order)
        except IntegrityError as ie:
            db.rollback()
            # Handle concurrent race condition where two identical idempotency requests arrived simultaneously
            if keys_to_check:
                existing_order = db.query(Order).options(
                    joinedload(Order.items)
                ).filter(
                    or_(
                        Order.idempotency_key.in_(keys_to_check),
                        Order.client_order_id.in_(keys_to_check)
                    )
                ).first()
                if existing_order:
                    table_number = None
                    if existing_order.table_id:
                        t = db.query(Table).filter(Table.id == existing_order.table_id).first()
                        if t:
                            table_number = t.table_number
                    return {
                        "orderId": existing_order.id,
                        "id": existing_order.id,
                        "branch_order_id": existing_order.branch_order_id,
                        "bill_no": existing_order.bill_no,
                        "status": existing_order.status,
                        "payment_status": existing_order.payment_status,
                        "payment_method": existing_order.payment_method,
                        "order_type": existing_order.order_type,
                        "total_amount": existing_order.total_amount,
                        "table_number": table_number,
                        "created_at": existing_order.created_at.isoformat() if existing_order.created_at else None,
                        "idempotency_key": existing_order.idempotency_key or primary_idempotency_key,
                        "client_order_id": existing_order.client_order_id or primary_idempotency_key,
                        "message": "Order already processed (idempotent replay)",
                        "idempotent": True,
                        "is_duplicate": True
                    }
            raise ie

        if cust_phone_clean:
            try:
                from ..models.customer import Customer
                cust = db.query(Customer).filter(Customer.phone == cust_phone_clean).first()
                if not cust and cust_name_clean:
                    db.add(Customer(phone=cust_phone_clean, name=cust_name_clean))
                    db.commit()
                elif cust and cust_name_clean and (not cust.name or cust.name == "Guest"):
                    cust.name = cust_name_clean
                    db.commit()
            except Exception as e:
                print("Customer sync note:", e)

        for item in payload.cart:
            order_item = OrderItem(
                order_id=new_order.id,
                menu_item_id=item.id,
                quantity=item.quantity,
                price=item.price,
                status="PENDING"
            )
            db.add(order_item)
        db.commit()

        return {
            "orderId": new_order.id,
            "id": new_order.id,
            "branch_order_id": new_order.branch_order_id,
            "bill_no": new_order.bill_no,
            "status": new_order.status,
            "total_amount": new_order.total_amount,
            "created_at": new_order.created_at.isoformat() if new_order.created_at else None,
            "idempotency_key": new_order.idempotency_key,
            "client_order_id": new_order.client_order_id,
            "message": "Order created successfully"
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}

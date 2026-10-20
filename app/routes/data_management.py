import os
import re
import calendar
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect

from ..db import SessionLocal
from ..models.order import Order
from ..models.dine_in_session import DineInSession
from ..models.catering import CateringOrder, CateringSession
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id
from ..utils.auth import SECRET_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/data-management", tags=["data-management"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─────────────────────────────────────────────────────────────────────────────
# Helper: IST date resolution & validation
# ─────────────────────────────────────────────────────────────────────────────

def get_ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def parse_and_validate_month(month_str: str) -> tuple[int, int, datetime, datetime, str, str]:
    """
    Validates month format (YYYY-MM), verifies it is strictly before the current month,
    and returns (year, month, utc_start, utc_end, date_range_display, month_name).
    """
    match = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", month_str.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid month format. Expected YYYY-MM (e.g. 2026-09)."
        )
    
    year = int(match.group(1))
    month = int(match.group(2))
    
    ist_now = get_ist_now()
    current_ym = (ist_now.year, ist_now.month)
    selected_ym = (year, month)
    
    if selected_ym >= current_ym:
        current_str = f"{ist_now.year:04d}-{ist_now.month:02d}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot delete data from the current month ({current_str}) or future months. "
                "Only completed previous calendar months are eligible for permanent deletion."
            )
        )
    
    # Month boundaries in IST
    start_ist = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        next_month_ist = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        next_month_ist = datetime(year, month + 1, 1, 0, 0, 0)
        
    last_day = calendar.monthrange(year, month)[1]
    date_range_display = f"01/{month:02d}/{year} 00:00:00 to {last_day:02d}/{month:02d}/{year} 23:59:59 (IST)"
    month_name = f"{calendar.month_name[month]} {year}"
    
    # Convert IST to UTC for database comparison
    utc_start = start_ist - timedelta(hours=5, minutes=30)
    utc_end = next_month_ist - timedelta(hours=5, minutes=30)
    
    return year, month, utc_start, utc_end, date_range_display, month_name


def generate_token(month: str, restaurant_id: Optional[int], total_records: int) -> str:
    timestamp = int(datetime.utcnow().timestamp())
    message = f"{month}:{restaurant_id}:{total_records}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}:{signature}"


def verify_token(token: str, month: str, restaurant_id: Optional[int], max_age_seconds: int = 900) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 5:
            return False
        t_month, t_rest, t_records, t_time_str, signature = parts
        if t_month != month:
            return False
        
        # Verify restaurant match
        token_rest_id = None if t_rest == "None" else int(t_rest)
        if token_rest_id != restaurant_id:
            return False
            
        timestamp = int(t_time_str)
        now = int(datetime.utcnow().timestamp())
        if (now - timestamp > max_age_seconds) or (timestamp > now + 60):
            return False
            
        expected_sig = hmac.new(
            SECRET_KEY.encode(), 
            f"{t_month}:{t_rest}:{t_records}:{t_time_str}".encode(), 
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


def chunk_list(lst: list, size: int = 1000):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ─────────────────────────────────────────────────────────────────────────────
# Inspection & Preview Logic
# ─────────────────────────────────────────────────────────────────────────────

def get_affected_counts(db: Session, utc_start: datetime, utc_end: datetime, restaurant_id: Optional[int] = None) -> tuple[Dict[str, int], list[int], list[int], list[str]]:
    """
    Safely calculates counts of rows across all tables affected by historical sales deletion.
    Returns: (counts_dict, order_ids, catering_order_ids, catering_session_ids)
    """
    bind = db.get_bind()
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())
    
    # 1. Base Orders Query
    order_query = db.query(Order.id).filter(
        Order.created_at >= utc_start,
        Order.created_at < utc_end
    )
    if restaurant_id:
        order_query = order_query.filter(Order.restaurant_id == restaurant_id)
        
    order_ids = [r[0] for r in order_query.all()]
    order_count = len(order_ids)
    
    table_counts = {}
    
    if order_ids:
        for chunk in chunk_list(order_ids, 1000):
            id_tuple = tuple(chunk) if len(chunk) > 1 else f"({chunk[0]})"
            
            # Helper to count in child table if table exists
            def count_in_table(table_name: str, col_name: str = "order_id") -> int:
                if table_name not in existing_tables:
                    return 0
                sql = f"SELECT COUNT(*) FROM {table_name} WHERE {col_name} IN {id_tuple}"
                return db.execute(text(sql)).scalar() or 0
                
            table_counts["order_items"] = table_counts.get("order_items", 0) + count_in_table("order_items")
            table_counts["payments"] = table_counts.get("payments", 0) + count_in_table("payments")
            table_counts["kot_tickets"] = table_counts.get("kot_tickets", 0) + count_in_table("kot_tickets")
            table_counts["loyalty_transactions"] = table_counts.get("loyalty_transactions", 0) + count_in_table("loyalty_transactions")
            table_counts["delivery_status_history"] = table_counts.get("delivery_status_history", 0) + count_in_table("delivery_status_history")
            table_counts["delivery_assignments"] = table_counts.get("delivery_assignments", 0) + count_in_table("delivery_assignments")
            table_counts["rider_locations"] = table_counts.get("rider_locations", 0) + count_in_table("rider_locations")
    else:
        for tbl in ["order_items", "payments", "kot_tickets", "loyalty_transactions", "delivery_status_history", "delivery_assignments", "rider_locations"]:
            if tbl in existing_tables:
                table_counts[tbl] = 0

    table_counts["orders"] = order_count
    
    # 2. Dine-in Sessions (sessions from that month that do not have active orders outside this month)
    dine_in_count = 0
    if "dine_in_sessions" in existing_tables:
        dis_q = db.query(DineInSession.id).filter(
            DineInSession.created_at >= utc_start,
            DineInSession.created_at < utc_end
        )
        if restaurant_id:
            dis_q = dis_q.filter(DineInSession.restaurant_id == restaurant_id)
        candidate_session_ids = [r[0] for r in dis_q.all()]
        
        # Verify no orders from other months reference these sessions
        if candidate_session_ids:
            for s_chunk in chunk_list(candidate_session_ids, 1000):
                s_tuple = tuple(s_chunk) if len(s_chunk) > 1 else f"({s_chunk[0]})"
                sql = f"""
                    SELECT id FROM dine_in_sessions 
                    WHERE id IN {s_tuple}
                    AND id NOT IN (
                        SELECT DISTINCT dine_in_session_id FROM orders 
                        WHERE dine_in_session_id IS NOT NULL 
                        AND (created_at < :u_start OR created_at >= :u_end)
                    )
                """
                matching = db.execute(text(sql), {"u_start": utc_start, "u_end": utc_end}).fetchall()
                dine_in_count += len(matching)
        table_counts["dine_in_sessions"] = dine_in_count

    # 3. Catering Orders & Sessions (if tables exist)
    cat_order_ids = []
    cat_session_ids = []
    if "catering_orders" in existing_tables:
        co_q = db.query(CateringOrder.id).filter(
            CateringOrder.created_at >= utc_start,
            CateringOrder.created_at < utc_end
        )
        if restaurant_id:
            co_q = co_q.filter(CateringOrder.restaurant_id == restaurant_id)
        cat_order_ids = [r[0] for r in co_q.all()]
        table_counts["catering_orders"] = len(cat_order_ids)
        
        if cat_order_ids:
            for co_chunk in chunk_list(cat_order_ids, 1000):
                co_tuple = tuple(co_chunk) if len(co_chunk) > 1 else f"({co_chunk[0]})"
                if "catering_payments" in existing_tables:
                    table_counts["catering_payments"] = table_counts.get("catering_payments", 0) + (
                        db.execute(text(f"SELECT COUNT(*) FROM catering_payments WHERE catering_order_id IN {co_tuple}")).scalar() or 0
                    )
                if "catering_order_addons" in existing_tables:
                    table_counts["catering_order_addons"] = table_counts.get("catering_order_addons", 0) + (
                        db.execute(text(f"SELECT COUNT(*) FROM catering_order_addons WHERE catering_order_id IN {co_tuple}")).scalar() or 0
                    )
                if "catering_order_customizations" in existing_tables:
                    table_counts["catering_order_customizations"] = table_counts.get("catering_order_customizations", 0) + (
                        db.execute(text(f"SELECT COUNT(*) FROM catering_order_customizations WHERE catering_order_id IN {co_tuple}")).scalar() or 0
                    )
        else:
            for c_tbl in ["catering_payments", "catering_order_addons", "catering_order_customizations"]:
                if c_tbl in existing_tables:
                    table_counts[c_tbl] = 0

    if "catering_sessions" in existing_tables:
        cs_q = db.query(CateringSession.id).filter(
            CateringSession.created_at >= utc_start,
            CateringSession.created_at < utc_end
        )
        if restaurant_id:
            cs_q = cs_q.filter(CateringSession.restaurant_id == restaurant_id)
        cat_session_ids = [str(r[0]) for r in cs_q.all()]
        table_counts["catering_sessions"] = len(cat_session_ids)
        
        if cat_session_ids:
            for cs_chunk in chunk_list(cat_session_ids, 1000):
                formatted = ", ".join(f"'{cid}'" for cid in cs_chunk)
                cs_tuple = f"({formatted})"
                if "catering_session_addons" in existing_tables:
                    table_counts["catering_session_addons"] = table_counts.get("catering_session_addons", 0) + (
                        db.execute(text(f"SELECT COUNT(*) FROM catering_session_addons WHERE catering_session_id IN {cs_tuple}")).scalar() or 0
                    )
                if "catering_session_customizations" in existing_tables:
                    table_counts["catering_session_customizations"] = table_counts.get("catering_session_customizations", 0) + (
                        db.execute(text(f"SELECT COUNT(*) FROM catering_session_customizations WHERE catering_session_id IN {cs_tuple}")).scalar() or 0
                    )
        else:
            for cs_tbl in ["catering_session_addons", "catering_session_customizations"]:
                if cs_tbl in existing_tables:
                    table_counts[cs_tbl] = 0

    return table_counts, order_ids, cat_order_ids, cat_session_ids


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/preview")
def preview_data_deletion(
    month: str = Query(..., description="Target month in YYYY-MM format"),
    restaurant_id: Optional[int] = Query(None, description="Optional restaurant ID (defaults to all for SUPER_ADMIN)"),
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Inspects and previews eligible historical data for a selected previous calendar month.
    Enforces that current-month or future-month data cannot be deleted.
    """
    require_role(user, ["SUPER_ADMIN", "ADMIN", "HOTEL_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    
    year, mon_num, utc_start, utc_end, date_range_display, month_name = parse_and_validate_month(month)
    
    counts_dict, order_ids, cat_order_ids, cat_session_ids = get_affected_counts(
        db, utc_start, utc_end, restaurant_id
    )
    
    total_records = sum(counts_dict.values())
    
    # Table descriptions for clear user preview
    descriptions = {
        "order_items": "Item lines, quantities, and rates from historical orders",
        "payments": "POS and online payment transaction records associated with orders",
        "kot_tickets": "Kitchen Order Ticket print queue records",
        "loyalty_transactions": "Loyalty point credit/debit records tied to deleted orders",
        "delivery_status_history": "Rider delivery timeline and tracking status logs",
        "delivery_assignments": "Rider order assignments and dispatch logs",
        "rider_locations": "GPS tracking breadcrumbs tied to orders",
        "orders": "Master orders, customer phone references, bills, and amounts",
        "dine_in_sessions": "Closed table sessions originating within the selected month",
        "catering_payments": "Payment logs for catering bookings in the selected month",
        "catering_order_addons": "Add-on selections for catering bookings",
        "catering_order_customizations": "Menu customization records for catering bookings",
        "catering_orders": "Catering event orders and booking contracts",
        "catering_session_addons": "Draft addon sessions for catering bookings",
        "catering_session_customizations": "Draft customization sessions for catering bookings",
        "catering_sessions": "Catering session quotes and inquiries",
    }
    
    affected_tables = []
    for tbl_name, cnt in counts_dict.items():
        affected_tables.append({
            "table_name": tbl_name,
            "estimated_count": cnt,
            "description": descriptions.get(tbl_name, "Historical database records")
        })
        
    # Sort with orders and items prominent
    affected_tables.sort(key=lambda x: (0 if x["table_name"] in ["orders", "order_items"] else 1, -x["estimated_count"]))
    
    token = generate_token(month, restaurant_id, total_records)
    required_phrase = f"PERMANENTLY DELETE {month} DATA"
    
    return {
        "month": month,
        "month_name": month_name,
        "date_range_display": date_range_display,
        "utc_range": {
            "start": utc_start.isoformat(),
            "end": utc_end.isoformat()
        },
        "affected_tables": affected_tables,
        "total_records": total_records,
        "confirmation_token": token,
        "required_confirm_phrase": required_phrase,
        "permanent_deletion_warning": (
            "WARNING: This action is permanent and completely irreversible. All selected records will be removed "
            "from the active PostgreSQL database. Please ensure you have taken a verified backup if historical reports "
            "or invoices are needed for tax, GST, or auditing compliance."
        ),
        "retention_advisory": (
            "Regulatory Notice: Business and tax laws in India (including GST rules) typically require preserving books "
            "of accounts, invoices, and payment receipts for up to 6–8 years. Deleting records from the live application "
            "will remove them from all in-app historical sales reports."
        )
    }


class DataDeletionRequest(BaseModel):
    month: str = Field(..., description="Target month in YYYY-MM format")
    restaurant_id: Optional[int] = Field(None, description="Optional restaurant ID")
    confirmation_token: str = Field(..., description="Security token generated by preview endpoint")
    confirm_phrase: str = Field(..., description="Exact match confirmation phrase")


@router.post("/delete")
def execute_data_deletion(
    req: DataDeletionRequest,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Safely executes permanent manual deletion of eligible historical data for a previous calendar month.
    Uses an atomic transaction and executes in child-first foreign-key dependency order.
    Rolls back automatically on any error.
    """
    require_role(user, ["SUPER_ADMIN", "ADMIN", "HOTEL_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, req.restaurant_id)
    
    year, mon_num, utc_start, utc_end, date_range_display, month_name = parse_and_validate_month(req.month)
    
    # 1. Validate confirmation phrase
    expected_phrase = f"PERMANENTLY DELETE {req.month} DATA"
    if req.confirm_phrase.strip().upper() != expected_phrase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Confirmation phrase mismatch. Please type exactly '{expected_phrase}' to proceed."
        )
        
    # 2. Validate confirmation token
    if not verify_token(req.confirmation_token, req.month, restaurant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation token has expired or is invalid. Please preview the data again before confirming deletion."
        )

    # 3. Perform atomic deletion in child-first order
    deleted_counts = {}
    total_deleted = 0
    start_time = datetime.utcnow()
    
    bind = db.get_bind()
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())
    
    try:
        # Re-fetch order IDs inside the transaction to avoid stale concurrency
        order_query = db.query(Order.id).filter(
            Order.created_at >= utc_start,
            Order.created_at < utc_end
        )
        if restaurant_id:
            order_query = order_query.filter(Order.restaurant_id == restaurant_id)
            
        order_ids = [r[0] for r in order_query.all()]
        
        # A. Delete Order Children First
        if order_ids:
            for chunk in chunk_list(order_ids, 1000):
                id_tuple = tuple(chunk) if len(chunk) > 1 else f"({chunk[0]})"
                
                # Helper for safe execution
                def run_delete(tbl: str, col: str = "order_id"):
                    if tbl in existing_tables:
                        res = db.execute(text(f"DELETE FROM {tbl} WHERE {col} IN {id_tuple}"))
                        deleted_counts[tbl] = deleted_counts.get(tbl, 0) + res.rowcount
                        
                run_delete("delivery_status_history")
                run_delete("delivery_assignments")
                run_delete("rider_locations")
                run_delete("kot_tickets")
                run_delete("loyalty_transactions")
                run_delete("payments")
                run_delete("order_items")
                run_delete("orders", "id")

        # B. Delete Dine-In Sessions created in that month that no longer have orders
        if "dine_in_sessions" in existing_tables:
            dis_q = db.query(DineInSession.id).filter(
                DineInSession.created_at >= utc_start,
                DineInSession.created_at < utc_end
            )
            if restaurant_id:
                dis_q = dis_q.filter(DineInSession.restaurant_id == restaurant_id)
            c_sess_ids = [r[0] for r in dis_q.all()]
            
            if c_sess_ids:
                for s_chunk in chunk_list(c_sess_ids, 1000):
                    s_tuple = tuple(s_chunk) if len(s_chunk) > 1 else f"({s_chunk[0]})"
                    del_dis_sql = f"""
                        DELETE FROM dine_in_sessions 
                        WHERE id IN {s_tuple}
                        AND id NOT IN (
                            SELECT DISTINCT dine_in_session_id FROM orders 
                            WHERE dine_in_session_id IS NOT NULL
                        )
                    """
                    res = db.execute(text(del_dis_sql))
                    deleted_counts["dine_in_sessions"] = deleted_counts.get("dine_in_sessions", 0) + res.rowcount

        # C. Delete Catering Records
        if "catering_orders" in existing_tables:
            co_q = db.query(CateringOrder.id).filter(
                CateringOrder.created_at >= utc_start,
                CateringOrder.created_at < utc_end
            )
            if restaurant_id:
                co_q = co_q.filter(CateringOrder.restaurant_id == restaurant_id)
            co_ids = [r[0] for r in co_q.all()]
            
            if co_ids:
                for co_chunk in chunk_list(co_ids, 1000):
                    co_tuple = tuple(co_chunk) if len(co_chunk) > 1 else f"({co_chunk[0]})"
                    if "catering_payments" in existing_tables:
                        res = db.execute(text(f"DELETE FROM catering_payments WHERE catering_order_id IN {co_tuple}"))
                        deleted_counts["catering_payments"] = deleted_counts.get("catering_payments", 0) + res.rowcount
                    if "catering_order_addons" in existing_tables:
                        res = db.execute(text(f"DELETE FROM catering_order_addons WHERE catering_order_id IN {co_tuple}"))
                        deleted_counts["catering_order_addons"] = deleted_counts.get("catering_order_addons", 0) + res.rowcount
                    if "catering_order_customizations" in existing_tables:
                        res = db.execute(text(f"DELETE FROM catering_order_customizations WHERE catering_order_id IN {co_tuple}"))
                        deleted_counts["catering_order_customizations"] = deleted_counts.get("catering_order_customizations", 0) + res.rowcount
                    res = db.execute(text(f"DELETE FROM catering_orders WHERE id IN {co_tuple}"))
                    deleted_counts["catering_orders"] = deleted_counts.get("catering_orders", 0) + res.rowcount

        if "catering_sessions" in existing_tables:
            cs_q = db.query(CateringSession.id).filter(
                CateringSession.created_at >= utc_start,
                CateringSession.created_at < utc_end
            )
            if restaurant_id:
                cs_q = cs_q.filter(CateringSession.restaurant_id == restaurant_id)
            cs_ids = [str(r[0]) for r in cs_q.all()]
            
            if cs_ids:
                for cs_chunk in chunk_list(cs_ids, 1000):
                    formatted = ", ".join(f"'{cid}'" for cid in cs_chunk)
                    cs_tuple = f"({formatted})"
                    if "catering_session_addons" in existing_tables:
                        res = db.execute(text(f"DELETE FROM catering_session_addons WHERE catering_session_id IN {cs_tuple}"))
                        deleted_counts["catering_session_addons"] = deleted_counts.get("catering_session_addons", 0) + res.rowcount
                    if "catering_session_customizations" in existing_tables:
                        res = db.execute(text(f"DELETE FROM catering_session_customizations WHERE catering_session_id IN {cs_tuple}"))
                        deleted_counts["catering_session_customizations"] = deleted_counts.get("catering_session_customizations", 0) + res.rowcount
                    res = db.execute(text(f"DELETE FROM catering_sessions WHERE id IN {cs_tuple}"))
                    deleted_counts["catering_sessions"] = deleted_counts.get("catering_sessions", 0) + res.rowcount

        # Commit transaction
        db.commit()
        
        total_deleted = sum(deleted_counts.values())
        elapsed_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        logger.info(
            f"ADMIN DATA DELETION COMPLETED: User '{user.email}' (role: {user.role}) permanently deleted "
            f"{total_deleted} records for month {req.month} in {elapsed_ms}ms."
        )
        
        return {
            "success": True,
            "month": req.month,
            "month_name": month_name,
            "message": f"Successfully deleted {total_deleted} eligible historical records for {month_name}.",
            "deleted_counts": deleted_counts,
            "total_deleted": total_deleted,
            "execution_time_ms": elapsed_ms,
            "deleted_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"ADMIN DATA DELETION FAILED AND ROLLED BACK: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Data deletion transaction failed and was rolled back: {str(e)}"
        )

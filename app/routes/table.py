from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from ..db import SessionLocal
from ..models.table import Table
from ..schemas.table import TableCreate, TableResponse, TableUpdate
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id, require_restaurant_access

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET all tables
@router.get("/api/v1/tables", response_model=list[TableResponse])
def get_tables(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(Table)
    if restaurant_id is not None:
        query = query.filter(Table.restaurant_id == restaurant_id)

    tables = query.order_by(Table.id).all()
    
    # Find all active orders for the relevant restaurant
    from ..models.order import Order
    active_orders_query = db.query(Order).filter(
        Order.status.notin_(("SERVED", "COMPLETED", "CANCELLED"))
    )
    if restaurant_id is not None:
        active_orders_query = active_orders_query.filter(Order.restaurant_id == restaurant_id)
        
    active_orders = active_orders_query.all()
    active_order_map = {}
    for o in active_orders:
        if o.table_id and str(o.table_id).isdigit():
            active_order_map[int(o.table_id)] = o.id

    result = []
    for t in tables:
        # Find active order
        active_order_id = active_order_map.get(t.id) if getattr(t, "is_active", True) else None
        
        result.append({
            "id": t.id,
            "restaurant_id": t.restaurant_id,
            "table_number": t.table_number,
            "qr_code": t.qr_code,
            "capacity": getattr(t, "capacity", 4) or 4,
            "status": getattr(t, "status", "Vacant") or "Vacant",
            "is_active": getattr(t, "is_active", True),
            "current_order_id": active_order_id,
            "created_at": t.created_at,
        })

    return result

# GET single table
@router.get("/api/v1/tables/{table_id}", response_model=TableResponse)
def get_table(table_id: int, user = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    table = db.query(Table).filter(Table.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    require_restaurant_access(user, table.restaurant_id)
    return table

# POST create table
@router.post("/api/v1/tables", response_model=TableResponse)
def create_table(data: TableCreate, user = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    table_data = data.model_dump()
    if user.role == "HOTEL_ADMIN":
        if table_data.get("restaurant_id") is not None and table_data["restaurant_id"] != user.restaurant_id:
            raise HTTPException(
                status_code=403,
                detail=f"Hotel admin can only create tables for restaurant {user.restaurant_id}"
            )
        table_data["restaurant_id"] = user.restaurant_id
    else:
        if not table_data.get("restaurant_id"):
            raise HTTPException(status_code=400, detail="restaurant_id is required for SUPER_ADMIN")

    table = Table(**table_data)
    db.add(table)
    db.commit()
    db.refresh(table)
    return table

# PATCH update table
@router.patch("/api/v1/tables/{table_id}", response_model=TableResponse)
def update_table(table_id: int, data: TableUpdate, user = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    table = db.query(Table).filter(Table.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    require_restaurant_access(user, table.restaurant_id)

    update_data = data.model_dump(exclude_unset=True)
    if user.role == "HOTEL_ADMIN" and update_data.get("restaurant_id") is not None and update_data["restaurant_id"] != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Hotel admin cannot move tables to restaurant {update_data['restaurant_id']}"
        )

    for key, value in update_data.items():
        setattr(table, key, value)

    db.commit()
    db.refresh(table)
    return table

# DELETE table
@router.delete("/api/v1/tables/{table_id}")
def delete_table(table_id: int, user = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    table = db.query(Table).filter(Table.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    require_restaurant_access(user, table.restaurant_id)
    db.delete(table)
    db.commit()

    return {"message": "Table deleted successfully"}

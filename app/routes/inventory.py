from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.inventory import InventoryItem
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id, require_restaurant_access
from ..utils.azure_scanner import AzureScanner
from fastapi import File, UploadFile
from typing import List

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/api/v1/inventory")
def get_inventory(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get inventory items with role-based access control

    - SUPER_ADMIN: Sees inventory from the selected restaurant or all restaurants if no restaurant_id is provided
    - HOTEL_ADMIN: Sees inventory only from their restaurant
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, restaurant_id)
    query = db.query(InventoryItem)
    if restaurant_id is not None:
        query = query.filter(InventoryItem.restaurant_id == restaurant_id)

    return query.all()


@router.patch("/api/v1/inventory/{inventory_id}")
def update_inventory(
    inventory_id: int,
    data: dict,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update inventory item with authorization check

    - SUPER_ADMIN: Can update items from any restaurant
    - HOTEL_ADMIN: Can only update items from their restaurant
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    item = db.query(InventoryItem).filter(InventoryItem.id == inventory_id).first()

    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    require_restaurant_access(user, item.restaurant_id)

    require_restaurant_access(user, item.restaurant_id)
 
    if "open_stock" in data:
        item.open_stock = data["open_stock"]
    if "purchase" in data:
        item.purchase = data["purchase"]
    if "total" in data:
        item.total = data["total"]
    if "issue" in data:
        item.issue = data["issue"]
    if "balance" in data:
        item.balance = data["balance"]
    if "name" in data:
        item.name = data["name"]
    if "unit" in data:
        item.unit = data["unit"]
 
    db.commit()
    db.refresh(item)
 
    return item


@router.delete("/api/v1/inventory/{inventory_id}")
def delete_inventory(
    inventory_id: int,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete inventory item with authorization check
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
 
    item = db.query(InventoryItem).filter(InventoryItem.id == inventory_id).first()
 
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
 
    require_restaurant_access(user, item.restaurant_id)
 
    db.delete(item)
    db.commit()
 
    return {"status": "success", "message": "Item deleted"}


@router.post("/api/v1/inventory")
def create_inventory(
    data: dict,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new inventory item
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    restaurant_id = resolve_restaurant_id(user, data.get("restaurant_id"))

    if not data.get("name"):
        raise HTTPException(status_code=400, detail="Name is required")

    new_item = InventoryItem(
        restaurant_id=restaurant_id,
        name=data["name"],
        open_stock=data.get("open_stock", 0.0),
        purchase=data.get("purchase", 0.0),
        total=data.get("total", 0.0),
        issue=data.get("issue", 0.0),
        balance=data.get("balance", 0.0),
        unit=data.get("unit", "units")
    )

    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    return new_item


@router.post("/api/v1/inventory/scan")
async def scan_inventory(
    front: UploadFile = File(...),
    back: UploadFile | None = File(None),
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Scan inventory sheets using Azure Document Intelligence
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    
    scanner = AzureScanner()
    results = []
    
    try:
        # Scan front side
        front_content = await front.read()
        front_items = scanner.scan_inventory_sheet(front_content)
        results.append(front_items)
        
        # Scan back side if provided
        if back:
            back_content = await back.read()
            back_items = scanner.scan_inventory_sheet(back_content)
            results.append(back_items)
            
        merged_items = scanner.merge_scanned_results(results)
        return merged_items
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/inventory/bulk")
def bulk_update_inventory(
    items: List[dict],
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Bulk create or update inventory items
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    
    restaurant_id = resolve_restaurant_id(user, None)
    
    updated_count = 0
    created_count = 0
    
    for item_data in items:
        name = item_data.get("name")
        open_stock = item_data.get("open_stock", 0.0)
        purchase = item_data.get("purchase", 0.0)
        total = item_data.get("total", 0.0)
        issue = item_data.get("issue", 0.0)
        balance = item_data.get("balance", 0.0)
        unit = item_data.get("unit")
        
        if not name:
            continue
            
        # Check if item exists
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.restaurant_id == restaurant_id,
            InventoryItem.name.ilike(name)
        ).first()
        
        if existing_item:
            existing_item.open_stock = open_stock
            existing_item.purchase = purchase
            existing_item.total = total
            existing_item.issue = issue
            existing_item.balance = balance
            if unit:
                existing_item.unit = unit
            updated_count += 1
        else:
            new_item = InventoryItem(
                restaurant_id=restaurant_id,
                name=name,
                open_stock=open_stock,
                purchase=purchase,
                total=total,
                issue=issue,
                balance=balance,
                unit=unit or "units"
            )
            db.add(new_item)
            created_count += 1
            
    db.commit()
    
    return {
        "status": "success",
        "message": f"Updated {updated_count} and created {created_count} items",
        "updated": updated_count,
        "created": created_count
    }
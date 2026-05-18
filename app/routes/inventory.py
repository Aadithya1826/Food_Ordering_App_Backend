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
 
    if "quantity" in data:
        item.quantity = data["quantity"]
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

    if not data.get("name") or data.get("quantity") is None or not data.get("unit"):
        raise HTTPException(status_code=400, detail="Name, quantity, and unit are required")

    new_item = InventoryItem(
        restaurant_id=restaurant_id,
        name=data["name"],
        quantity=data["quantity"],
        unit=data["unit"]
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
        quantity = item_data.get("quantity")
        unit = item_data.get("unit")
        
        if not name or quantity is None:
            continue
            
        # Check if item exists
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.restaurant_id == restaurant_id,
            InventoryItem.name.ilike(name)
        ).first()
        
        if existing_item:
            existing_item.quantity = quantity
            if unit:
                existing_item.unit = unit
            updated_count += 1
        else:
            new_item = InventoryItem(
                restaurant_id=restaurant_id,
                name=name,
                quantity=quantity,
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
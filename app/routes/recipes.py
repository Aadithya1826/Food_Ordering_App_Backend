from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, date

from ..db import SessionLocal
from ..models.recipe import RecipeIngredient
from ..models.order import Order, OrderItem
from ..models.inventory import InventoryItem
from ..models.menu import MenuItem
from ..schemas.recipe import RecipeIngredientResponse, RecipeUpdatePayload
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/api/v1/recipes", response_model=List[RecipeIngredientResponse])
def get_recipes(
    menu_item_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get recipes. If menu_item_id is provided, returns recipes for that specific item.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, None)
    
    query = db.query(RecipeIngredient).filter(RecipeIngredient.restaurant_id == restaurant_id)
    if menu_item_id:
        query = query.filter(RecipeIngredient.menu_item_id == menu_item_id)
        
    return query.all()

@router.post("/api/v1/recipes")
def update_recipe(
    payload: RecipeUpdatePayload,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Overwrites the recipe for a specific menu item.
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, None)
    
    # Verify menu item exists and belongs to restaurant
    menu_item = db.query(MenuItem).filter(
        MenuItem.id == payload.menu_item_id,
        MenuItem.restaurant_id == restaurant_id
    ).first()
    
    if not menu_item:
        raise HTTPException(status_code=404, detail="Menu item not found")
        
    # Delete existing recipe ingredients for this menu item
    db.query(RecipeIngredient).filter(
        RecipeIngredient.menu_item_id == payload.menu_item_id
    ).delete()
    
    # Add new ones
    for ing in payload.ingredients:
        new_ing = RecipeIngredient(
            restaurant_id=restaurant_id,
            menu_item_id=payload.menu_item_id,
            inventory_item_name=ing.inventory_item_name,
            quantity=ing.quantity,
            unit=ing.unit
        )
        db.add(new_ing)
        
    db.commit()
    return {"status": "success", "message": f"Recipe updated for {menu_item.name}"}


@router.get("/api/v1/reports/consumption")
def get_consumption_report(
    report_date: str,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Calculates Theoretical vs Actual Consumption for a given date.
    Actual Consumption = (Open Stock + Purchase) - Balance
    Theoretical Consumption = Sum of (Qty Sold * Recipe Qty)
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, None)
    
    try:
        parsed_date = datetime.strptime(report_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
    # 1. Calculate items sold on this date
    # Join OrderItem with Order to filter by Order.created_at
    items_sold_query = db.query(
        OrderItem.menu_item_id,
        MenuItem.name.label("menu_item_name"),
        func.sum(OrderItem.quantity).label("total_sold")
    ).join(
        Order, Order.id == OrderItem.order_id
    ).join(
        MenuItem, MenuItem.id == OrderItem.menu_item_id
    ).filter(
        Order.restaurant_id == restaurant_id,
        func.date(Order.created_at) == parsed_date,
        Order.status != "CANCELLED"
    ).group_by(OrderItem.menu_item_id, MenuItem.name).all()
    
    items_sold_map = {row.menu_item_id: {"name": row.menu_item_name, "qty": row.total_sold} for row in items_sold_query}
    
    # 2. Calculate Theoretical Consumption
    # We need all recipes for items that were sold
    theoretical_consumption = {}
    if items_sold_map:
        sold_menu_item_ids = list(items_sold_map.keys())
        recipes = db.query(RecipeIngredient).filter(
            RecipeIngredient.restaurant_id == restaurant_id,
            RecipeIngredient.menu_item_id.in_(sold_menu_item_ids)
        ).all()
        
        for recipe in recipes:
            qty_sold = items_sold_map[recipe.menu_item_id]["qty"]
            theoretical_qty = qty_sold * recipe.quantity
            
            if recipe.inventory_item_name in theoretical_consumption:
                theoretical_consumption[recipe.inventory_item_name]["quantity"] += theoretical_qty
            else:
                theoretical_consumption[recipe.inventory_item_name] = {
                    "quantity": theoretical_qty,
                    "unit": recipe.unit
                }
                
    # 3. Fetch Actual Consumption from Inventory for this date
    inventory_items = db.query(InventoryItem).filter(
        InventoryItem.restaurant_id == restaurant_id,
        InventoryItem.report_date == parsed_date
    ).all()
    
    actual_consumption_map = {}
    for item in inventory_items:
        # Actual Consumption = (Open + Purchase) - Balance
        actual_qty = (item.open_stock + item.purchase) - item.balance
        actual_consumption_map[item.name] = {
            "quantity": actual_qty,
            "unit": item.unit
        }
        
    # 4. Tally everything together
    all_ingredients = set(list(theoretical_consumption.keys()) + list(actual_consumption_map.keys()))
    
    tally = []
    for ing_name in all_ingredients:
        theo = theoretical_consumption.get(ing_name, {"quantity": 0, "unit": ""})
        act = actual_consumption_map.get(ing_name, {"quantity": 0, "unit": theo.get("unit", "")})
        
        # Prefer the unit from actual if exists, else theoretical
        unit = act["unit"] if act["unit"] else theo["unit"]
        
        variance = act["quantity"] - theo["quantity"]
        
        tally.append({
            "ingredient_name": ing_name,
            "theoretical_consumption": theo["quantity"],
            "actual_consumption": act["quantity"],
            "variance": variance,
            "unit": unit
        })
        
    return {
        "date": report_date,
        "items_sold": [{"id": k, "name": v["name"], "quantity": v["qty"]} for k, v in items_sold_map.items()],
        "tally": tally
    }

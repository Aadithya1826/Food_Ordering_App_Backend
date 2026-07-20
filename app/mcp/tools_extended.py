import bcrypt
from sqlalchemy.orm import Session
from fastapi import HTTPException

from ..models.menu import MenuCategory, MenuItem
from ..models.order import Order, OrderItem
from ..models.table import Table
from ..models.inventory import InventoryItem
from ..models.user import User
from ..models.restaurant import Restaurant
from ..utils.roles import filter_by_user_restaurant, require_role, require_restaurant_access

# Orders & Payments
def get_orders(db: Session, user, status: str | None = None) -> list[dict]:
    query = filter_by_user_restaurant(user, db.query(Order))
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.created_at.desc()).all()
    return [
        {
            "id": o.id, "status": o.status, "payment_status": o.payment_status,
            "total_amount": float(o.total_amount), "table_id": o.table_id
        } for o in orders
    ]

def update_payment_status(db: Session, user, order_id: int, payment_status: str, payment_method: str | None = None) -> dict:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order: raise HTTPException(404, "Order not found")
    require_restaurant_access(user, order.restaurant_id)
    order.payment_status = payment_status
    if payment_method:
        order.payment_method = payment_method
    db.commit()
    return {"message": f"Order {order_id} payment status updated to {payment_status}"}

# Inventory
def get_inventory(db: Session, user) -> list[dict]:
    query = filter_by_user_restaurant(user, db.query(InventoryItem))
    return [{"id": i.id, "name": i.name, "balance": float(i.balance), "unit": i.unit} for i in query.all()]

def create_inventory_item(db: Session, user, name: str, unit: str, open_stock: float = 0.0) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    # Determine correct restaurant_id
    restaurant_id = user.restaurant_id if user.restaurant_id else 1 # Default to 1 if super admin didn't provide
    item = InventoryItem(name=name, unit=unit, open_stock=open_stock, balance=open_stock, restaurant_id=restaurant_id)
    db.add(item)
    db.commit()
    return {"message": f"Inventory item {name} created", "id": item.id}

def delete_inventory_item(db: Session, user, item_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item: raise HTTPException(404, "Item not found")
    require_restaurant_access(user, item.restaurant_id)
    db.delete(item)
    db.commit()
    return {"message": f"Inventory item {item.name} deleted"}

# Menu
def create_menu_item(db: Session, user, name: str, price: float, category_id: int, description: str = "", is_veg: bool = True) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    cat = db.query(MenuCategory).filter(MenuCategory.id == category_id).first()
    if not cat: raise HTTPException(404, "Category not found")
    require_restaurant_access(user, cat.restaurant_id)
    item = MenuItem(name=name, price=price, category_id=category_id, description=description, is_veg=is_veg, restaurant_id=cat.restaurant_id)
    db.add(item)
    db.commit()
    return {"message": f"Menu item {name} created", "id": item.id}

def delete_menu_item(db: Session, user, item_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item: raise HTTPException(404, "Item not found")
    require_restaurant_access(user, item.restaurant_id)
    db.delete(item)
    db.commit()
    return {"message": f"Menu item {item.name} deleted"}

# Tables
def get_tables(db: Session, user) -> list[dict]:
    query = filter_by_user_restaurant(user, db.query(Table))
    return [{"id": t.id, "table_number": t.table_number, "status": t.status, "capacity": t.capacity} for t in query.all()]

def create_table(db: Session, user, table_number: str, capacity: int = 4) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    restaurant_id = user.restaurant_id if user.restaurant_id else 1
    table = Table(table_number=table_number, capacity=capacity, status="Vacant", restaurant_id=restaurant_id)
    db.add(table)
    db.commit()
    return {"message": f"Table {table_number} created", "id": table.id}

def delete_table(db: Session, user, table_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN", "HOTEL_ADMIN"])
    table = db.query(Table).filter(Table.id == table_id).first()
    if not table: raise HTTPException(404, "Table not found")
    require_restaurant_access(user, table.restaurant_id)
    db.delete(table)
    db.commit()
    return {"message": f"Table {table.table_number} deleted"}

# Reports
from datetime import datetime, timedelta

def get_reports(db: Session, user, timeframe: str = "today") -> dict:
    query = filter_by_user_restaurant(user, db.query(Order))
    
    now = datetime.utcnow()
    if timeframe == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif timeframe == "this_week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif timeframe == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    # If timeframe is 'all_time' or anything else, we don't filter by date.
        
    orders = query.all()
    total_rev = sum(float(o.total_amount) for o in orders if o.payment_status == 'Paid')
    return {
        "timeframe": timeframe,
        "total_revenue": total_rev, 
        "total_orders": len(orders),
        "paid_orders": len([o for o in orders if o.payment_status == 'Paid']),
        "pending_orders": len([o for o in orders if o.payment_status != 'Paid'])
    }

def get_item_sales_report(db: Session, user, item_name: str | None = None, timeframe: str = "today") -> list[dict]:
    query = filter_by_user_restaurant(user, db.query(Order))
    
    now = datetime.utcnow()
    if timeframe == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif timeframe == "this_week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
    elif timeframe == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.filter(Order.created_at >= start_date)
        
    orders = query.all()
    sales_data = {}
    
    for o in orders:
        if o.payment_status == 'Paid':
            for item in o.items:
                if item.menu_item:
                    name = item.menu_item.name
                    if item_name and item_name.lower() not in name.lower():
                        continue
                    if name not in sales_data:
                        sales_data[name] = {"quantity_sold": 0, "revenue": 0.0}
                    sales_data[name]["quantity_sold"] += item.quantity
                    sales_data[name]["revenue"] += float(item.price) * item.quantity

    results = [{"item_name": k, "quantity_sold": v["quantity_sold"], "revenue": v["revenue"]} for k, v in sales_data.items()]
    results.sort(key=lambda x: x["revenue"], reverse=True)
    return results

# Restaurants
def create_restaurant(db: Session, user, name: str, address: str = None, phone: str = None) -> dict:
    require_role(user, ["SUPER_ADMIN"])
    existing = db.query(Restaurant).filter(Restaurant.name == name).first()
    if existing: raise HTTPException(400, "Restaurant name already exists")
    restaurant = Restaurant(name=name, address=address, phone=phone)
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return {"message": f"Hotel {name} created", "id": restaurant.id}

def update_restaurant(db: Session, user, restaurant_id: int, name: str = None, address: str = None, phone: str = None) -> dict:
    require_role(user, ["SUPER_ADMIN"])
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant: raise HTTPException(404, "Restaurant not found")
    if name is not None:
        restaurant.name = name
    if address is not None:
        restaurant.address = address
    if phone is not None:
        restaurant.phone = phone
    db.commit()
    return {"message": f"Hotel {restaurant.name} updated"}

def delete_restaurant(db: Session, user, restaurant_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN"])
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant: raise HTTPException(404, "Restaurant not found")
    
    db.query(User).filter(User.restaurant_id == restaurant_id).update({"restaurant_id": None})
    db.query(InventoryItem).filter(InventoryItem.restaurant_id == restaurant_id).delete(synchronize_session=False)

    order_ids = db.query(Order.id).filter(Order.restaurant_id == restaurant_id).all()
    order_ids = [o[0] for o in order_ids]
    if order_ids:
        db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)

    db.query(Order).filter(Order.restaurant_id == restaurant_id).delete(synchronize_session=False)
    db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id).delete(synchronize_session=False)
    db.query(MenuCategory).filter(MenuCategory.restaurant_id == restaurant_id).delete(synchronize_session=False)
    db.query(Table).filter(Table.restaurant_id == restaurant_id).delete(synchronize_session=False)

    db.delete(restaurant)
    db.commit()
    return {"message": f"Hotel {restaurant.name} deleted"}

# Managers
def get_managers(db: Session, user) -> list[dict]:
    require_role(user, ["SUPER_ADMIN"])
    managers = db.query(User).filter(User.role == "HOTEL_ADMIN").all()
    return [{"id": m.id, "name": m.name, "email": m.email, "restaurant_id": m.restaurant_id} for m in managers]

def create_manager(db: Session, user, name: str, email: str, password: str, phone: str, restaurant_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN"])
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    mgr = User(name=name, email=email, hashed_password=hashed, phone=phone, role="HOTEL_ADMIN", restaurant_id=restaurant_id)
    db.add(mgr)
    db.commit()
    return {"message": f"Manager {name} created for restaurant {restaurant_id}"}

def delete_manager(db: Session, user, manager_id: int) -> dict:
    require_role(user, ["SUPER_ADMIN"])
    mgr = db.query(User).filter(User.id == manager_id).first()
    if not mgr: raise HTTPException(404, "Manager not found")
    db.delete(mgr)
    db.commit()
    return {"message": f"Manager {mgr.name} deleted"}


EXTENDED_TOOLS = {
    "get_orders": {
        "description": "Get a list of orders, optionally filtered by status.",
        "parameters": {
            "status": "Optional. Status to filter by (e.g., 'Preparing', 'Ready', 'Completed')."
        },
        "handler": get_orders,
    },
    "update_payment_status": {
        "description": "Update the payment status and method of an order.",
        "parameters": {
            "order_id": "The ID of the order.",
            "payment_status": "New payment status ('Paid', 'Unpaid').",
            "payment_method": "Optional. Method of payment ('Cash', 'Card', 'UPI')."
        },
        "handler": update_payment_status,
    },
    "get_inventory": {
        "description": "List all inventory items and their current stock balances.",
        "parameters": {},
        "handler": get_inventory,
    },
    "create_inventory_item": {
        "description": "Create a new inventory item.",
        "parameters": {
            "name": "Name of the item.",
            "unit": "Unit of measurement (e.g., 'kg', 'ltr', 'pcs').",
            "open_stock": "Optional. Initial stock amount."
        },
        "handler": create_inventory_item,
    },
    "delete_inventory_item": {
        "description": "Delete an inventory item.",
        "parameters": {
            "item_id": "The ID of the inventory item to delete."
        },
        "handler": delete_inventory_item,
    },
    "create_menu_item": {
        "description": "Create a new menu item.",
        "parameters": {
            "name": "Name of the dish.",
            "price": "Price of the dish.",
            "category_id": "ID of the menu category it belongs to.",
            "description": "Optional. Description of the dish.",
            "is_veg": "Optional. Boolean indicating if it's vegetarian."
        },
        "handler": create_menu_item,
    },
    "delete_menu_item": {
        "description": "Delete a menu item.",
        "parameters": {
            "item_id": "The ID of the menu item to delete."
        },
        "handler": delete_menu_item,
    },
    "get_tables": {
        "description": "List all tables and their statuses.",
        "parameters": {},
        "handler": get_tables,
    },
    "create_table": {
        "description": "Create a new table.",
        "parameters": {
            "table_number": "Table identifier string (e.g. 'T-10').",
            "capacity": "Optional. Seating capacity."
        },
        "handler": create_table,
    },
    "delete_table": {
        "description": "Delete a table.",
        "parameters": {
            "table_id": "The ID of the table to delete."
        },
        "handler": delete_table,
    },
    "get_reports": {
        "description": "Get summary metrics like total revenue, paid orders, and pending orders.",
        "parameters": {
            "timeframe": "Optional. Timeframe for the report (e.g., 'today', 'this_week', 'this_month', 'all_time')."
        },
        "handler": get_reports,
    },
    "get_item_sales_report": {
        "description": "Get sales data (quantity sold, total revenue) for a specific menu item or all items.",
        "parameters": {
            "item_name": "Optional. The specific menu item name to look up. If omitted, returns data for all items.",
            "timeframe": "Optional. Timeframe for the report (e.g., 'today', 'this_week', 'this_month', 'all_time')."
        },
        "handler": get_item_sales_report,
    },
    "get_managers": {
        "description": "SUPER_ADMIN ONLY. List all hotel managers.",
        "parameters": {},
        "handler": get_managers,
    },
    "create_manager": {
        "description": "SUPER_ADMIN ONLY. Create a new hotel manager.",
        "parameters": {
            "name": "Manager's name.",
            "email": "Manager's email address.",
            "password": "Password for the new account.",
            "phone": "Phone number.",
            "restaurant_id": "ID of the restaurant they will manage."
        },
        "handler": create_manager,
    },
    "delete_manager": {
        "description": "SUPER_ADMIN ONLY. Delete a hotel manager.",
        "parameters": {
            "manager_id": "ID of the manager to delete."
        },
        "handler": delete_manager,
    },
    "create_restaurant": {
        "description": "SUPER_ADMIN ONLY. Create a new hotel/restaurant.",
        "parameters": {
            "name": "Name of the hotel.",
            "address": "Optional. Address of the hotel.",
            "phone": "Optional. Phone number."
        },
        "handler": create_restaurant,
    },
    "update_restaurant": {
        "description": "SUPER_ADMIN ONLY. Update an existing hotel/restaurant.",
        "parameters": {
            "restaurant_id": "ID of the hotel to update.",
            "name": "Optional. New name of the hotel.",
            "address": "Optional. New address.",
            "phone": "Optional. New phone number."
        },
        "handler": update_restaurant,
    },
    "delete_restaurant": {
        "description": "SUPER_ADMIN ONLY. Delete a hotel/restaurant and all its associated data.",
        "parameters": {
            "restaurant_id": "ID of the hotel to delete."
        },
        "handler": delete_restaurant,
    }
}

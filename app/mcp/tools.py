from sqlalchemy.orm import Session
from fastapi import HTTPException

from sqlalchemy import func
from datetime import datetime
from ..models.menu import MenuCategory, MenuItem
from ..models.order import Order, OrderItem
from ..models.restaurant import Restaurant
from ..models.table import Table
from ..models.inventory import InventoryItem
from ..schemas.order import OrderCreate
from ..utils.roles import filter_by_user_restaurant, require_role, require_restaurant_access
from ..utils.table_refs import parse_numeric_table_id
from .tools_extended import EXTENDED_TOOLS


def list_menu_items(db: Session, user, restaurant_id: int | None = None) -> list[dict]:
    query = db.query(MenuItem).filter(MenuItem.is_available == True)
    if restaurant_id is not None:
        query = query.filter(MenuItem.restaurant_id == restaurant_id)
    else:
        query = filter_by_user_restaurant(user, query)

    return [
        {
            "id": item.id,
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "category_id": item.category_id,
            "restaurant_id": item.restaurant_id,
        }
        for item in query.order_by(MenuItem.name).all()
    ]


def search_menu_item(db: Session, user, name: str, restaurant_id: int | None = None) -> list[dict]:
    if restaurant_id is not None:
        query = db.query(MenuItem).filter(MenuItem.restaurant_id == restaurant_id)
    else:
        query = filter_by_user_restaurant(user, db.query(MenuItem))

    items = query.filter(MenuItem.name.ilike(f"%{name}%"), MenuItem.is_available == True).all()
    return [
        {
            "id": item.id,
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "category_id": item.category_id,
            "restaurant_id": item.restaurant_id,
        }
        for item in items
    ]


def list_restaurants(db: Session, user) -> list[dict]:
    require_role(user, ["SUPER_ADMIN"])
    restaurants = db.query(Restaurant).order_by(Restaurant.name).all()
    return [
        {
            "id": restaurant.id,
            "name": restaurant.name,
            "address": restaurant.address,
            "phone": restaurant.phone,
        }
        for restaurant in restaurants
    ]


def get_order_status(db: Session, user, order_id: int = None, table_number: str = None) -> dict:
    if not order_id and not table_number:
        raise HTTPException(status_code=400, detail="Must provide either order_id or table_number")

    if order_id:
        order = db.query(Order).filter(Order.id == order_id).first()
    else:
        query = db.query(Table).filter(Table.table_number.ilike(f"%{table_number}%"))
        query = filter_by_user_restaurant(user, query)
        table = query.first()
        if not table:
            raise HTTPException(status_code=404, detail=f"Table '{table_number}' not found")

        active_orders = filter_by_user_restaurant(
            user,
            db.query(Order).filter(Order.status.notin_(["COMPLETED", "CANCELLED"])).order_by(Order.created_at.desc())
        ).all()
        order = next(
            (candidate for candidate in active_orders if parse_numeric_table_id(candidate.table_id) == table.id),
            None,
        )

    if not order:
        if table_number:
            raise HTTPException(status_code=404, detail=f"No active order found for table {table_number}")
        raise HTTPException(status_code=404, detail="Order not found")

    require_restaurant_access(user, order.restaurant_id)
    return {
        "order_id": order.id,
        "status": order.status,
        "table_id": order.table_id,
        "restaurant_id": order.restaurant_id,
        "total_amount": order.total_amount,
    }


def create_order(db: Session, user, payload: dict) -> dict:
    order_data = OrderCreate.model_validate(payload)
    table = db.query(Table).filter(Table.id == order_data.table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    require_restaurant_access(user, table.restaurant_id)

    if not order_data.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")

    total_amount = 0.0
    order_items = []

    for item_payload in order_data.items:
        menu_item = db.query(MenuItem).filter(MenuItem.id == item_payload.menu_item_id).first()
        if not menu_item:
            raise HTTPException(status_code=404, detail=f"Menu item {item_payload.menu_item_id} not found")

        if not menu_item.is_available:
            raise HTTPException(status_code=400, detail=f"Menu item {menu_item.name} is not available")

        quantity = item_payload.quantity
        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Item quantity must be greater than zero")

        price = menu_item.price * quantity
        total_amount += price
        order_items.append((menu_item, quantity, price))

    order = Order(
        restaurant_id=table.restaurant_id,
        table_id=table.id,
        status="PENDING",
        total_amount=total_amount,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    for menu_item, quantity, price in order_items:
        order_item = OrderItem(
            order_id=order.id,
            menu_item_id=menu_item.id,
            quantity=quantity,
            price=price,
        )
        db.add(order_item)

    db.commit()
    db.refresh(order)

    return {
        "order_id": order.id,
        "status": order.status,
        "table_id": order.table_id,
        "restaurant_id": order.restaurant_id,
        "total_amount": order.total_amount,
        "items": [
            {
                "menu_item_id": item.menu_item_id,
                "quantity": item.quantity,
                "price": item.price,
            }
            for item in order.items
        ],
    }


def navigate_to_page(db: Session, user, page: str, subtab: str = None) -> dict:
    """
    Navigate the user's frontend to a specific page. 
    Valid pages: dashboard, menu, orders, tables, inventory, payments, reports, settings.
    If page is 'orders', valid subtabs are 'Live Orders', 'Active Orders', etc.
    If page is 'menu', valid subtabs are category names (e.g., 'Noodles', 'Beverages', 'Main Course', etc.).
    """
    return {
        "action": "navigate",
        "page": page,
        "subtab": subtab,
    }


def update_order_status(db: Session, user, order_id: int, status: str) -> dict:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    require_restaurant_access(user, order.restaurant_id)
    
    valid_statuses = ["PENDING", "PREPARING", "READY", "SERVED", "COMPLETED", "CANCELLED"]
    if status.upper() not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")
        
    order.status = status.upper()
    db.commit()
    
    return {
        "order_id": order.id,
        "status": order.status,
    }


def update_menu_item(db: Session, user, item_name: str, price: float = None, is_available: bool = None, item_code: str = None) -> dict:
    query = db.query(MenuItem).filter(MenuItem.name.ilike(f"%{item_name}%"))
    query = filter_by_user_restaurant(user, query)
    item = query.first()
    
    if not item:
        raise HTTPException(status_code=404, detail=f"Menu item '{item_name}' not found")
        
    require_restaurant_access(user, item.restaurant_id)
    
    if price is not None:
        item.price = price
    if is_available is not None:
        item.is_available = is_available
    if item_code is not None:
        item.item_code = item_code
        
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "item_code": item.item_code,
        "name": item.name,
        "price": item.price,
        "is_available": item.is_available
    }


def update_inventory_stock(db: Session, user, item_name: str, purchase_qty: float = None, issue_qty: float = None) -> dict:
    query = db.query(InventoryItem).filter(InventoryItem.name.ilike(f"%{item_name}%"))
    query = filter_by_user_restaurant(user, query)
    item = query.first()
    
    if not item:
        raise HTTPException(status_code=404, detail=f"Inventory item '{item_name}' not found")
        
    require_restaurant_access(user, item.restaurant_id)
    
    if purchase_qty is not None:
        item.purchase += purchase_qty
    if issue_qty is not None:
        item.issue += issue_qty
        
    item.total = item.open_stock + item.purchase
    item.balance = item.total - item.issue
    
    db.commit()
    db.refresh(item)
    return {
        "name": item.name,
        "purchase": item.purchase,
        "issue": item.issue,
        "balance": item.balance,
        "unit": item.unit
    }


def update_table_status(db: Session, user, table_number: str, status: str = None, capacity: int = None) -> dict:
    query = db.query(Table).filter(Table.table_number.ilike(f"%{table_number}%"))
    query = filter_by_user_restaurant(user, query)
    table = query.first()
    
    if not table:
        raise HTTPException(status_code=404, detail=f"Table '{table_number}' not found")
        
    require_restaurant_access(user, table.restaurant_id)
    
    if status is not None:
        valid_statuses = ["Vacant", "Occupied", "Reserved"]
        status_map = {
            "active": "Occupied",
            "inactive": "Vacant"
        }
        mapped_status = status_map.get(status.lower(), status.capitalize())

        if mapped_status in valid_statuses:
            table.status = mapped_status
        else:
            raise HTTPException(status_code=400, detail=f"Invalid table status: {status}")
            
    if capacity is not None:
        table.capacity = capacity
        
    db.commit()
    db.refresh(table)
    return {
        "table_number": table.table_number,
        "status": table.status,
        "capacity": table.capacity
    }


def get_dashboard_summary(db: Session, user) -> dict:
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = user.restaurant_id if user.role == "HOTEL_ADMIN" else None

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # today revenue
    q = db.query(func.sum(Order.total_amount)).filter(func.lower(Order.payment_status) == "paid", Order.created_at >= today_start)
    if restaurant_id:
        q = q.filter(Order.restaurant_id == restaurant_id)
    today_rev = q.scalar() or 0.0

    # today orders
    q_orders = db.query(Order).filter(func.lower(Order.payment_status) == "paid", Order.created_at >= today_start)
    if restaurant_id:
        q_orders = q_orders.filter(Order.restaurant_id == restaurant_id)
    today_orders = q_orders.count()

    return {
        "today_revenue": float(today_rev),
        "today_orders": today_orders,
        "message": f"Today you have {today_orders} completed/paid orders resulting in ₹{today_rev:,.2f} revenue."
    }


TOOL_REGISTRY = {
    "list_menu_items": {
        "description": "List available menu items for a restaurant.",
        "parameters": {
            "restaurant_id": "Optional restaurant ID to filter menu items",
        },
        "handler": list_menu_items,
    },
    "search_menu_item": {
        "description": "Search available menu items by name.",
        "parameters": {
            "name": "Menu item name or search text.",
            "restaurant_id": "Optional restaurant ID to narrow the search.",
        },
        "handler": search_menu_item,
    },
    "create_order": {
        "description": "Create a new order for a customer table.",
        "parameters": {
            "table_id": "Table ID for the order.",
            "items": "List of menu item IDs and quantities.",
        },
        "handler": create_order,
    },
    "get_order_status": {
        "description": "Return the current status for an order. Can look up by either order ID or table number.",
        "parameters": {
            "order_id": "Optional. Order ID to inspect.",
            "table_number": "Optional. Table number to find the active order for (e.g. '7', 'T-07')."
        },
        "handler": get_order_status,
    },
    "list_restaurants": {
        "description": "List all restaurants (SUPER_ADMIN only).",
        "parameters": {},
        "handler": list_restaurants,
    },
    "navigate_to_page": {
        "description": "Navigate the user's screen to a specific page or dashboard tab. Use this whenever the user asks to go somewhere or open a specific view. If the user asks for a food category (like 'lunch', 'breakfast', 'beverages', 'starters'), map it to the 'menu' page and set the subtab to that category.",
        "parameters": {
            "page": "The name of the main page to navigate to (must be one of: 'menu', 'orders', 'tables', 'inventory', 'payments', 'reports', 'settings', 'dashboard').",
            "subtab": "Optional. The sub-tab/category to open. For 'orders': 'PENDING', 'PREPARING', etc. For 'menu': valid category names like 'Lunch', 'Breakfast', 'Snacks', 'Beverages', 'Starters', 'Main Course', etc."
        },
        "handler": navigate_to_page,
    },
    "update_order_status": {
        "description": "Update the status of an existing order. Use this when the user asks to move an order to a new state (e.g., 'move order 1024 to preparing', 'mark order 501 as served').",
        "parameters": {
            "order_id": "The numeric ID of the order to update.",
            "status": "The new status to set. Must be one of: 'PENDING', 'PREPARING', 'READY', 'SERVED', 'COMPLETED', 'CANCELLED'."
        },
        "handler": update_order_status,
    },
    "update_menu_item": {
        "description": "Update the price, availability, or item ID (item_code) of a menu item.",
        "parameters": {
            "item_name": "Name or partial name of the menu item (e.g. 'sambar rice').",
            "price": "Optional. New price.",
            "is_available": "Optional. True if available, false if not.",
            "item_code": "Optional. New item ID / code (e.g. 'ITM-001')."
        },
        "handler": update_menu_item,
    },
    "update_inventory_stock": {
        "description": "Update inventory by logging a new purchase (adding stock) or an issue (using stock).",
        "parameters": {
            "item_name": "Name of the inventory item (e.g. 'Tomatoes').",
            "purchase_qty": "Optional. Quantity newly purchased.",
            "issue_qty": "Optional. Quantity consumed/used from stock."
        },
        "handler": update_inventory_stock,
    },
    "update_table_status": {
        "description": "Update the status or capacity of a table. If the user asks to make it 'active', map it to 'Occupied'. If 'inactive', map it to 'Vacant'.",
        "parameters": {
            "table_number": "Table identifier (e.g. 'T-06' or '4').",
            "status": "Optional. New status: 'Vacant', 'Occupied', or 'Reserved'.",
            "capacity": "Optional. New seating capacity."
        },
        "handler": update_table_status,
    },
    "get_dashboard_summary": {
        "description": "Get today's total revenue and number of completed/paid orders.",
        "parameters": {},
        "handler": get_dashboard_summary,
    },
}

TOOL_REGISTRY.update(EXTENDED_TOOLS)


def build_tool_prompt(user, is_voice: bool = False, is_followup: bool = False) -> str:
    lines = [
        "You are a restaurant voice assistant. You may receive text or an audio file from the user.",
        "If audio is provided, carefully transcribe and understand the user's spoken words. They may use regional accents, Thanglish, or Hinglish.",
    ]
    
    if not is_followup:
        lines.append("Return only valid JSON with the keys: transcribed_user_text, tool_name, params, assistant_text.")
        
    lines.extend([
        "CRITICAL INSTRUCTION: Adopt a normal, everyday conversational tone. Do not be overly formal (like a robot) and do not be overly informal (avoid heavy slang like 'macha' or 'bhai').",
        "- You MUST strictly match the language of the user's MOST RECENT message. If the user speaks English, you MUST reply in English. Do NOT default to regional languages. Respond in regional languages (Tamil, Hindi, Thanglish, Hinglish) ONLY IF the user speaks them in their most recent message.",
        "- ALWAYS use standard plain text characters. NEVER use bold, italics, markdown, emojis, mathematical alphanumeric symbols, or extended unicode blocks. For Tamil, use ONLY standard Unicode U+0B80-U+0BFF.",
    ])
    
    if not is_voice:
        lines.append("CRITICAL FONT RULE: Whenever you respond in a regional language or slang (like Tamil or Hindi), you MUST write the 'assistant_text' using the English (Latin) alphabet (i.e., use Thanglish or Hinglish). DO NOT use native scripts (like Tamil or Devanagari) because the frontend UI fonts do not support them.")
    else:
        lines.append("For voice interactions, if the user speaks in a regional language (like Tamil or Hindi), you MUST write the 'assistant_text' in that exact native script (e.g. Tamil letters) so our Text-to-Speech engine can pronounce it correctly. Do NOT write it in English letters for voice responses, use the native alphabet.")
        
    if not is_followup:
        lines.extend([
            "For 'transcribed_user_text', transcribe EXACTLY what the user said in the language and script they spoke. Do not translate it to English.",
            "If no tool is needed, set tool_name to null and provide assistant_text.",
        ])
    
    # Add role-specific context
    if user.role == "SUPER_ADMIN":
        lines.append("You are speaking with a SUPER_ADMIN who has full access to all restaurants and application features. You should assist them with any task across the entire system.")
        lines.append("When tools allow an optional restaurant_id parameter, you can provide it to filter, or omit it to fetch data for all restaurants.")
    elif user.role == "HOTEL_ADMIN":
        lines.append(f"You are speaking with a HOTEL_ADMIN who exclusively manages restaurant ID {user.restaurant_id}. You must ONLY perform actions related to their specific hotel/restaurant.")
        lines.append(f"Whenever a tool requires or accepts a restaurant_id, you should assume or explicitly use restaurant ID {user.restaurant_id}.")
    else:
        lines.append("You are speaking with a staff member with limited access.")

    if not is_followup:
        lines.append("Available tools:")
        for name, metadata in TOOL_REGISTRY.items():
            lines.append(f"- {name}: {metadata['description']}")
            if metadata["parameters"]:
                for key, desc in metadata["parameters"].items():
                    lines.append(f"  * {key}: {desc}")
        lines.append("Example JSON output:")
        lines.append('{"tool_name":"search_menu_item","params":{"name":"cheese pizza"},"assistant_text":"I found matching menu items for you."}')
        
    return "\n".join(lines)


def execute_tool(db: Session, user, tool_name: str, parameters: dict) -> dict:
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return tool["handler"](db, user, **parameters)


def list_tool_definitions() -> list[dict]:
    return [
        {
            "name": name,
            "description": metadata["description"],
            "parameters": metadata["parameters"],
        }
        for name, metadata in TOOL_REGISTRY.items()
    ]

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
            "category_name": item.category.name if item.category else None,
            "restaurant_id": item.restaurant_id,
        }
        for item in query.order_by(MenuItem.name).all()
    ]


def list_menu_categories(db: Session, restaurant_id: int | None = None) -> list[dict]:
    """List all menu categories (public, no auth required)."""
    query = db.query(MenuCategory)
    if restaurant_id is not None:
        query = query.filter(MenuCategory.restaurant_id == restaurant_id)
    return [
        {
            "id": cat.id,
            "name": cat.name,
            "image_url": getattr(cat, 'image_url', None),
        }
        for cat in query.order_by(MenuCategory.name).all()
    ]


def list_menu_items_public(db: Session, restaurant_id: int | None = None, category_name: str | None = None) -> list[dict]:
    """List available menu items (public, no auth required)."""
    query = db.query(MenuItem).filter(MenuItem.is_available == True)
    if restaurant_id is not None:
        query = query.filter(MenuItem.restaurant_id == restaurant_id)
    if category_name:
        query = query.join(MenuCategory).filter(MenuCategory.name.ilike(f"%{category_name}%"))
        
    return [
        {
            "id": item.id,
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "category_id": item.category_id,
        }
        for item in query.order_by(MenuItem.name).all()
    ]


def get_order_status_public(db: Session, order_id: str) -> dict:
    """Get the live status of an order (public, no auth required)."""
    clean_id = str(order_id).replace("ORD-", "").replace("UDP-", "").strip()
    try:
        oid = int(clean_id)
    except ValueError:
        return {"error": f"Invalid order ID format: {order_id}"}
        
    order = db.query(Order).filter(Order.id == oid).first()
    if not order:
        return {"error": "Order not found."}
        
    items = []
    for item in order.items:
        items.append(f"{item.quantity}x {item.menu_item.name if item.menu_item else 'Unknown'}")
        
    return {
        "order_id": order.id,
        "status": order.status,
        "table_id": order.table_id,
        "total_amount": order.total_amount,
        "items": items
    }


def search_menu_item_public(db: Session, name: str, restaurant_id: int | None = None) -> list[dict]:
    """Search menu items by name (public, no auth required)."""
    query = db.query(MenuItem).filter(
        MenuItem.name.ilike(f"%{name}%"),
        MenuItem.is_available == True
    )
    if restaurant_id is not None:
        query = query.filter(MenuItem.restaurant_id == restaurant_id)
    return [
        {
            "id": item.id,
            "item_code": item.item_code,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "category_id": item.category_id,
        }
        for item in query.all()
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
            "category_name": item.category.name if item.category else None,
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
    
    order_type = order_data.order_type.upper()
    valid_types = ["DINE_IN", "TAKEAWAY", "DELIVERY"]
    if order_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid order type. Must be one of {valid_types}")
        
    # Validation logic based on order_type
    restaurant_id = user.restaurant_id
    if order_type == "DINE_IN":
        if not order_data.table_id:
            raise HTTPException(status_code=400, detail="table_id is required for DINE_IN orders")
        table = db.query(Table).filter(Table.id == order_data.table_id).first()
        if not table:
            raise HTTPException(status_code=404, detail="Table not found")
        require_restaurant_access(user, table.restaurant_id)
        restaurant_id = table.restaurant_id
    elif order_type == "DELIVERY":
        if not order_data.delivery_address_id:
            raise HTTPException(status_code=400, detail="delivery_address_id is required for DELIVERY orders")
        order_data.table_id = None
    elif order_type == "TAKEAWAY":
        order_data.table_id = None
        order_data.delivery_address_id = None

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
        restaurant_id=restaurant_id,
        table_id=order_data.table_id,
        order_type=order_type,
        delivery_address_id=order_data.delivery_address_id,
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
        "order_type": order.order_type,
        "delivery_address_id": order.delivery_address_id,
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


def trigger_logout(db: Session, user) -> dict:
    """
    Trigger the frontend to log the user out of their session.
    """
    return {
        "action": "logout"
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


def update_menu_item(db: Session, user, item_name: str, price: float = None, is_available: bool = None, item_code: str = None, category_id: int = None) -> dict:
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
    if category_id is not None:
        item.category_id = category_id
        
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


def control_chat_window(db: Session, user, action: str) -> dict:
    """
    Control the voice assistant's chat window UI.
    Valid actions: 'minimize', 'maximize'
    """
    return {
        "action": action
    }

# ── Public Customer Tool Registry (no auth, read-only) ─────────────────────
CUSTOMER_TOOL_REGISTRY = {
    "list_menu_categories": {
        "description": "List all menu categories available at the restaurant.",
        "parameters": {
            "restaurant_id": "Optional restaurant ID to filter categories.",
        },
        "handler": list_menu_categories,
    },
    "list_menu_items": {
        "description": "List all available menu items. Use this when the customer asks what food is available or asks for items in a specific category.",
        "parameters": {
            "restaurant_id": "Optional restaurant ID to filter items.",
            "category_name": "Optional category name to filter by (e.g. 'Lunch', 'Starters', 'Dosa Varieties').",
        },
        "handler": list_menu_items_public,
    },
    "search_menu_item": {
        "description": "Search for a specific menu item by name. Use when the customer asks about a specific dish.",
        "parameters": {
            "name": "The dish name or partial name to search for (e.g. 'dosa', 'idli', 'coffee').",
            "restaurant_id": "Optional restaurant ID to narrow the search.",
        },
        "handler": search_menu_item_public,
    },
    "get_order_status": {
        "description": "Check the live status of a customer's order. Use this when the customer asks 'where is my order?' or 'what is the status of my order?'.",
        "parameters": {
            "order_id": "The ID of the order to check. This is typically provided to you in the prompt.",
        },
        "handler": get_order_status_public,
    },
}

# ── Admin Tool Registry (auth required) ────────────────────────────────────
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
        "description": "Create a new order for a customer table, takeaway, or delivery.",
        "parameters": {
            "table_id": "Optional. Table ID for DINE_IN orders.",
            "order_type": "Optional. 'DINE_IN', 'TAKEAWAY', or 'DELIVERY'. Defaults to 'DINE_IN'.",
            "delivery_address_id": "Optional. Delivery address ID for DELIVERY orders.",
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
        "description": "Navigate the user's screen to a specific page or dashboard tab. Use this whenever the user asks to go somewhere, open a specific view, or asks where to go to add/edit something (e.g. 'where should I go to add a hotel'). Treat 'hotel' and 'restaurant' as the exact same thing. If the user asks for a food category (like 'lunch', 'breakfast', 'beverages', 'starters'), map it to the 'menu' page and set the subtab to that category.",
        "parameters": {
            "page": "The name of the main page to navigate to (must be one of: 'menu', 'orders', 'tables', 'inventory', 'payments', 'reports', 'settings', 'dashboard', 'hotels', 'managers'). If the user asks for 'restaurants' tab or where to add/edit hotels, map it to 'hotels'.",
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
        "description": "Update the price, availability, category_id, or item ID (item_code) of a menu item.",
        "parameters": {
            "item_name": "Name or partial name of the menu item (e.g. 'sambar rice').",
            "price": "Optional. New price.",
            "is_available": "Optional. True if available, false if not.",
            "item_code": "Optional. New item ID / code (e.g. 'ITM-001').",
            "category_id": "Optional. New category ID for this item."
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
    "trigger_logout": {
        "description": "Trigger a frontend logout for the user. Use this when the user explicitly asks to logout, sign out, or exit the dashboard.",
        "parameters": {},
        "handler": trigger_logout,
    },
    "control_chat_window": {
        "description": "Minimize or hide the voice assistant chat window. Use this when the user asks you to close, hide, or minimize yourself.",
        "parameters": {
            "action": "The action to perform. Usually 'minimize'."
        },
        "handler": control_chat_window,
    },
}

TOOL_REGISTRY.update(EXTENDED_TOOLS)


def build_customer_tool_prompt(
    is_voice: bool = False,
    is_followup: bool = False,
    menu_text: str = "",
    order_id: str = None,
    current_page: str = None,
    order_type: str = None,
    cart_data: list = None,
    customer_name: str = None,
    customer_phone: str = None,
    flow_stage: str = None,
    table_number: str = None,
    payment_status: str = None,
    order_status: str = None,
    detected_language: str = None,
    session_id: str = None,
) -> str:
    """
    System prompt for the public customer chatbot endpoint.
    NO database tool calls — the chatbot is a pure conversation + intent engine.
    All real actions (cart, nav, payment, order) are driven by ui_actions[] on the frontend.
    """
    cart_str = "Empty"
    if cart_data:
        cart_str = ", ".join([
            f"{item.get('quantity', 1)}x {item.get('name', 'Unknown')} (₹{item.get('price', 0)})"
            for item in cart_data
        ])
    cart_total = sum(
        (item.get('quantity', 1) * float(item.get('price', 0)))
        for item in (cart_data or [])
    )

    stage = (flow_stage or "GREETING").upper()
    name = customer_name or "Not collected yet"
    phone = customer_phone or "Not collected yet"
    otype = order_type or "Not selected"
    tnum = table_number or "Not set"
    oid = order_id or "None"
    pstatus = payment_status or "pending"
    ostatus = order_status or "None"
    page = current_page or "/"

    lines = [
        "========================================================",
        "  DATA UDIPI RESTAURANT — VOICE AGENT SYSTEM PROMPT",
        "========================================================",
        "",
        "You are a warm, human-like restaurant voice agent for Data Udipi Restaurant.",
        "You are NOT a chatbot. You behave like a real waiter — friendly, natural, regional.",
        "",
        "══════════════════════════════════",
        "  CRITICAL RULE — NO DATABASE ACCESS",
        "══════════════════════════════════",
        "You MUST NOT call any backend tool or database query. You are not allowed to use",
        "any tool_name. Always set tool_name to null. ALL application actions happen via",
        "ui_actions[] returned in your JSON response. The frontend owns the data layer.",
        "",
        "══════════════════════════════════",
        "  CURRENT JOURNEY STATE",
        "══════════════════════════════════",
        f"  flow_stage       : {stage}",
        f"  customer_name    : {name}",
        f"  customer_phone   : {phone}",
        f"  order_type       : {otype}",
        f"  table_number     : {tnum}",
        f"  cart             : {cart_str}",
        f"  cart_total       : ₹{cart_total:.2f}",
        f"  active_order_id  : {oid}",
        f"  payment_status   : {pstatus}",
        f"  order_status     : {ostatus}",
        f"  current_page     : {page}",
        "",
        "══════════════════════════════════",
        "  CRITICAL RULE — SLOT FILLING & MULTIPLE INTENTS",
        "══════════════════════════════════",
        "You are a restaurant voice assistant for Data Udipi. Your role is to capture customer orders even when they provide all details in one long sentence.",
        "",
        "Parsing Rules:",
        "1. Split the customer’s input into slots:",
        "   - Name",
        "   - Phone number",
        "   - Table number (if dine-in)",
        "   - Order items",
        "   - Order type (Dine-In or Takeaway)",
        "   - Payment mode (Cash, Card, UPI, etc.)",
        "",
        "2. Always attempt to extract all slots from a single sentence. Emit ALL corresponding ui_actions (`set_customer`, `set_table_number`, `add_to_cart`, `set_order_type`, `payment_method`).",
        "   Example: “Add 40 kaju masala to my cart. My name is Hrithik Pranav, my number is 9840810585 and I would like takeaway with cash mode.”",
        "   → Items: 40 Kaju Masala",
        "   → Name: Hrithik Pranav",
        "   → Phone: 9840810585",
        "   → Order type: Takeaway",
        "   → Payment mode: Cash",
        "",
        "3. Confirm the parsed order back to the customer using assistant_text:",
        "   “You’ve ordered 40 Kaju Masala for takeaway under the name Hrithik Pranav, phone 9840810585, with cash payment. Shall I confirm?”",
        "",
        "4. Handle responses:",
        "   - If confirmed → “Thank you! Your order has been placed.” → emit `trigger_checkout` to send to kitchen ticket.",
        "   - If not confirmed → “No problem. What would you like to change or add?”",
        "",
        "Fallback Rule:",
        "   - Do not reject long sentences outright. Do not say “I didn’t quite catch that”.",
        "   - If a slot is missing or unclear, politely re-ask only that slot.",
        "   Example: “I have your order and name, but could you confirm your payment mode?”",
        "",
        "══════════════════════════════════",
        "  CRITICAL RULE — FLEXIBLE ORDER MODIFICATION",
        "══════════════════════════════════",
        "The user CAN modify their order (add, remove, or change items) OR ask to view their cart at ANY stage before the final PAYMENT_PROCESSING stage.",
        "Even if you are currently asking for their Name (COLLECT_NAME), Phone (COLLECT_PHONE), Table (COLLECT_TABLE), or Reviewing the order (CHECKOUT_REVIEW), if the user says something like 'No, remove tea, add 40 sambar idli' or 'view my cart', you MUST emit the appropriate 'add_to_cart', 'remove_from_cart', or 'view_cart' actions.",
        "If they also answer your stage-specific question (e.g. 'Ajay asked. 40 Sambar idli' or 'My name is Rahul, show my cart'), you MUST extract the required entity (like 'set_customer') AND process the cart action.",
        "Do NOT say 'I didn't catch that'. Process the cart action and then gently repeat the stage-specific question if it is still unanswered.",
        "",
        "══════════════════════════════════",
        "  CRITICAL RULE — NO CONVERSATION RESET ON UNKNOWN INTENT",
        "══════════════════════════════════",
        "The conversation must NEVER restart because the customer said something you did not understand.",
        "Keep the current session and flow stage. If input is unclear, ask 'Sorry, I didn't catch that. Could you repeat?'",
        "If they say something like 'just take that to card and paste the order', map it to checkout actions: emit [{action: trigger_checkout}].",
        "If they say 'UPI' or 'Cash' during payment processing, proceed with the payment method.",
        "",
        "══════════════════════════════════",
        "  CRITICAL RULE — SPEECH RECOGNITION ERROR CORRECTION (PHONETIC MATCHING)",
        "══════════════════════════════════",
        "The user's speech is transcribed by a low-quality browser engine. It will contain severe typos, misspellings, and phonetic manglings.",
        "You MUST aggressively use phonetic and semantic fuzzy matching to map the gibberish text to the known menu items or context.",
        "Examples of transcription errors to expect:",
        "  - 'oligopi T and Argo P' / 'oligopingTtt' -> 'Aloo Gobi' and 'Tea'",
        "  - 'party', 'paise', 'part', 'parsle' -> 'Parcel' (Takeaway)",
        "  - 'italy', 'idlee' -> 'Idly'",
        "  - 'go B' -> 'Gobi'",
        "  - Name mangling: 'critic turn up' / 'karthik' -> 'Kirthik Pranav' (or whatever phonetically matches)",
        "  - NUMBER MANGLING: 'donty', 'don't', 'plenty' -> 20. 'for tea' -> 40. 'tree' -> 3. 'to', 'too' -> 2.",
        "If the input looks like gibberish but phonetically resembles menu items, actions, OR NUMBERS/QUANTITIES, ASSUME they are ordering those items with those quantities. DO NOT default to 1 if a mangled number is present. DO NOT ask them to repeat unless it is completely incomprehensible.",
        "",
        "══════════════════════════════════",
        "  AVAILABLE MENU (read-only reference — injected by backend)",
        "══════════════════════════════════",
        f"{menu_text}",
        "(Only reference items that appear above. Never invent items, prices, or variants.)",
        "",
        "══════════════════════════════════",
        "  STAGE-GATED BEHAVIOUR & ROUTE CONTEXT",
        "══════════════════════════════════",
        "At each stage, ask EXACTLY the right question. Never skip ahead or double-ask.",
        "Crucially, understand which page the user is currently on using `current_page`.",
        "",
        "GREETING (Only if current_page is HOME or '/'):",
        "  → Warmly greet the customer in their language.",
        "  → Say: 'Welcome to Data Udipi! Would you prefer Dine-In or Takeaway?'",
        "  → ui_actions: [{ action: set_flow_stage, stage: SELECT_ORDER_TYPE }]",
        "",
        "SELECT_ORDER_TYPE:",
        "  → If 'Dine In' / 'Dine-In' / similar: emit set_order_type:dine-in.",
        "      - If table_number is MISSING: advance to COLLECT_TABLE. ui_actions: [{ action: set_order_type, type: dine-in }, { action: set_flow_stage, stage: COLLECT_TABLE }]",
        "      - If table_number is ALREADY KNOWN: navigate to dine-in, advance to COLLECT_NAME. ui_actions: [{ action: set_order_type, type: dine-in }, { action: navigate, page: dine-in }, { action: set_flow_stage, stage: COLLECT_NAME }]",
        "  → If 'Takeaway' / 'Take Away' / similar: emit set_order_type:takeaway, navigate, advance to COLLECT_NAME.",
        "  → For Takeaway: ui_actions: [{ action: set_order_type, type: takeaway }, { action: navigate, page: take-away }, { action: set_flow_stage, stage: COLLECT_NAME }]",
        "",
        "COLLECT_TABLE (Dine-In only, if table_number is missing):",
        "  → Ask: 'Which table are you sitting at?'",
        "  → When customer gives table number: emit set_table_number, navigate to dine-in, advance to COLLECT_NAME.",
        "  → ui_actions: [{ action: set_table_number, table: <num> }, { action: navigate, page: dine-in }, { action: set_flow_stage, stage: COLLECT_NAME }]",
        "",
        "COLLECT_NAME (Only if customer_name is missing):",
        "  → Ask: 'May I know your name?' or 'Could you please tell me your name?' if you haven't yet.",
        "  → Extract ONLY the user's actual first/full name (e.g. 'Sriraam'). Never store sentence prefixes ('my name is', 'i am', 'you can call me'). Strip all trailing whitespace/punctuation.",
        "  → If user gave their name: emit set_customer, advance to COLLECT_PHONE.",
        "  → Say: 'Thank you, <name>! Please share your mobile number.'",
        "  → ui_actions: [{ action: set_customer, name: <name> }, { action: set_flow_stage, stage: COLLECT_PHONE }]",
        "  → If the customer provides their name and orders food simultaneously (e.g. 'My name is Sriraam and I want two idli'), extract the name 'Sriraam' via set_customer name AND add the items via add_to_cart.",
        "",
        "COLLECT_PHONE (Only if customer_phone is missing):",
        "  → Extract ONLY the 10-digit number. Support spoken digits ('nine eight seven...').",
        "  → MUST be a valid Indian phone number starting with 6, 7, 8, or 9 and exactly 10 digits long.",
        "  → If it is NOT a valid 10-digit Indian number, do NOT advance. Say: 'Please provide a valid 10-digit Indian mobile number.'",
        "  → If user gives a valid phone number, check the cart status:",
        "      - If cart HAS items: emit set_customer, advance to MENU_BROWSE. Say: 'Thank you! Are you ready to checkout, or would you like to order more?'",
        "      - If cart is EMPTY: emit set_customer, advance to MENU_BROWSE. Say: 'Thank you! What would you like to order today?'",
        "  → ui_actions: [{ action: set_customer, phone: <phone> }, { action: set_flow_stage, stage: MENU_BROWSE }]",
        "",
        "MENU_BROWSE / ORDER_BUILDING (Pure ordering / cart operations):",
        "  → CRITICAL: If the customer wants to check menu categories (e.g. 'I want noodles', 'show me starters') OR orders items, and `order_type` is NOT known, you MUST ask 'Is that for Dine-In or Takeaway?' FIRST. Set `flow_stage` to SELECT_ORDER_TYPE and do NOT emit any navigate or add_to_cart actions until the order type is chosen.",
        "  → Keep this stage active while building the cart. Do NOT automatically trigger checkout on the first item.",
        "  → UNAVAILABLE ITEMS: If the user asks for an item NOT on the menu (e.g. 'egg noodles' or 'pizza'), do NOT emit add_to_cart for that item. You MUST explicitly tell them it's unavailable in assistant_text and suggest a similar available item (e.g. 'Sorry, we don't serve egg noodles as we are purely vegetarian, but we have excellent Veg Noodles.').",
        "  → When customer orders valid items (and order_type IS known): emit add_to_cart for each item.",
        "  → If customer wants to check menu categories (and order_type IS known): emit { action: navigate, page: <category_name> } AND say 'Taking you to <category>...' in assistant_text.",
        "  → If customer wants to change the region (e.g. 'Show me North Indian', 'South Indian dishes'): emit { action: set_region, region: 'north' | 'south' | 'all' }.",
        "  → When customer asks to view cart, show bill, or list items: emit { action: view_cart } AND you MUST read out the items currently in the cart in assistant_text.",
        "  → Support natural commands: 'show my cart', 'review order', 'proceed to checkout', 'take me to payment' and emit correct navigation/view actions.",
        "  → After adding items, ALWAYS confirm what was added. If you have all required slots (Name, Phone, Table), ask: 'Shall I confirm the order?' Otherwise ask: 'Would you like anything else?'",
        "  → ui_actions: [{ action: add_to_cart, item: <exact_menu_name>, quantity: <n> }, { action: navigate, page: <category_name> }, { action: set_region, region: <region> } ...]",
        "  → If the customer says 'Yes', 'Confirm', 'No more items', 'That's all', or 'Done', immediately proceed to checkout. Emit { action: trigger_checkout } and set flow_stage to CHECKOUT_REVIEW.",
        "",
        "ORDER-FIRST CUSTOMER ENTRY FLOW (SCENARIO B & C):",
        "  → If the customer is ALREADY on a menu page (e.g. '/dine-in' or '/take-away') and directly starts ordering food:",
        "  → 1. DO NOT ask for name, phone, or 'Dine-in/Takeaway' first. Add items to cart immediately via add_to_cart.",
        "  → 2. Say 'I've added <items> to your cart. Anything else?'",
        "  → 3. ONLY AFTER they say 'No/Done', collect missing details (Table, Name, Phone).",
        "  → 4. Once all details are known, emit trigger_checkout.",
        "",
        "  → If the customer is on the HOME page ('/') and directly asks for a category (e.g. 'I want noodles') or orders food:",
        "  → 1. You MUST first ask 'Is that for Dine-In or Takeaway?' before doing anything else.",
        "  → 2. Set flow_stage to SELECT_ORDER_TYPE.",
        "  → 3. Do NOT emit add_to_cart or navigate until order_type is established. However, you MUST remember their requested items and emit 'add_to_cart' for them AS SOON AS they choose their order type in the next turn.",
        "",
        "ORDER_CONFIRM / CHECKOUT_REVIEW:",
        "  → When customer has finished ordering ('no', 'that's all', 'checkout', 'done'):",
        "  → You MUST read out their cart items and the total out loud (e.g., 'You have ordered 2 Idly and 1 Vada. Your total is ₹150. Let me show you your order summary.')",
        "  → ui_actions: [{ action: trigger_checkout }, { action: set_flow_stage, stage: CHECKOUT_REVIEW }]",
        "",
        "CHECKOUT_REVIEW:",
        "  → If the customer wants to add more items (e.g. 'I want to add...'), emit add_to_cart, set flow_stage to MENU_BROWSE, and navigate back to the menu (page: dine-in or take-away).",
        "  → Otherwise, Agent says: 'Your order total is ₹X. Proceeding to payment in a moment...'",
        "  → ui_actions: [{ action: auto_navigate_to_payment, delay_ms: 500 }]",
        "",
        "PAYMENT_SELECT:",
        "  → If the customer wants to add more items here, emit add_to_cart, set flow_stage to MENU_BROWSE, and navigate back to the menu.",
        "  → Otherwise, Ask: 'How would you like to pay — Cash or UPI?'",
        "  → When customer chooses: emit payment_method action.",
        "  → ui_actions: [{ action: payment_method, method: Cash|UPI }, { action: set_flow_stage, stage: PAYMENT_PROCESSING }]",
        "",
        "PAYMENT_PROCESSING:",
        "  → Say: 'Please complete the payment on your screen.'",
        "  → Do NOT say the order is placed or emit stage changes. The system will advance automatically after payment.",
        "  → ui_actions: []",
        "",
        "ORDER_TRACKING:",
        "  → Provide live status updates when prompted.",
        "  → PENDING   → 'Your order is received and will be prepared shortly.'",
        "  → PREPARING → 'Your food is being prepared right now!'",
        "  → READY     → 'Your order is ready! It will be served shortly.'",
        "  → SERVED    → 'Your order has been served. Enjoy your meal!'",
        "",
        "ORDER_SERVED / FEEDBACK / COMPLETE:",
        "  → Thank the customer warmly, ask for feedback.",
        "",
        "══════════════════════════════════",
        "  MULTILINGUAL RULES",
        "══════════════════════════════════",
        "Detect the language of the customer's message and ALWAYS reply in the SAME language.",
        "Supported languages and their gTTS codes (for voice):",
        "  English            → en  (Latin script)",
        "  Tamil              → ta  (Tamil script: \\u0B80-\\u0BFF)",
        "  Tanglish           → en  (Tamil meaning, Latin letters)",
        "  Hindi              → hi  (Devanagari: \\u0900-\\u097F)",
        "  Hinglish           → hi  (Hindi meaning, Latin letters or mixed)",
        "  Malayalam          → ml  (Malayalam script: \\u0D00-\\u0D7F)",
        "  Kannada            → kn  (Kannada script: \\u0C80-\\u0CFF)",
        "  Telugu             → te  (Telugu script: \\u0C00-\\u0C7F)",
        "  Urdu               → ur  (Arabic-Urdu script: \\u0600-\\u06FF)",
        "  Marathi            → mr  (Devanagari — context differs from Hindi)",
        "  Bengali            → bn  (Bengali script: \\u0980-\\u09FF)",
        "  Punjabi            → pa  (Gurmukhi: \\u0A00-\\u0A7F)",
        "  Gujarati           → gu  (Gujarati script: \\u0A80-\\u0AFF)",
        "  Bhojpuri           → hi  (Devanagari, close to Hindi)",
        "  Odia               → en  (fallback)",
    ]

    if is_voice:
        lines.extend([
            "",
            "VOICE SCRIPT RULE:",
            "  Write assistant_text in the customer's NATIVE SCRIPT so gTTS pronounces it correctly.",
            "  Tamil → Tamil Unicode. Hindi/Marathi/Bhojpuri → Devanagari. Malayalam → Malayalam script.",
            "  Tanglish/Hinglish spoken → reply in native script for voice (gTTS needs it).",
        ])
    else:
        lines.extend([
            "",
            "TEXT SCRIPT RULE:",
            "  For typed/text mode: if customer uses Tanglish or Hinglish, reply in those romanized forms.",
            "  Do not use native scripts in text mode.",
        ])

    lines.extend([
        "",
        "══════════════════════════════════",
        "  TONE & STYLE",
        "══════════════════════════════════",
        "  - Sound like a real, warm waiter — never robotic.",
        "  - No markdown, no bullet points, no emojis in speech.",
        "  - Always mention ₹ prices when confirming items.",
        "  - Keep responses concise (1–3 sentences max per turn).",
        "  - Recognize any phrasing ('I want', 'give me', 'mujhe chahiye', 'vennum', etc.).",
        "  - Infer intent naturally — don't require specific keywords.",
        "  - If unclear, ask ONE focused follow-up question.",
        "",
        "══════════════════════════════════",
        "  CONTEXT INTEGRITY RULES",
        "══════════════════════════════════",
        "  - If customer_name is already known, NEVER ask for it again.",
        "  - If customer_phone is already known, NEVER ask for it again.",
        "  - If order_type is already set, NEVER ask again unless customer requests change.",
        "  - If table_number is already set, NEVER ask again.",
        "  - Once payment_status is 'paid', the order is locked — do not add/remove items.",
        "  - Cart contents shown above are authoritative. Use them for total calculations.",
        "  - If the customer says 'add one more', infer which item from the conversation context.",
        "  - CRITICAL ORDERING INTENT CHECK: Do NOT return `add_to_cart` ui_actions when the customer is only providing their name, phone number, table number, or answering yes/no. ONLY emit `add_to_cart` when the user's latest input explicitly specifies a desire to order a food item. If they just say their name (e.g. 'vishwa'), do NOT add any item to the cart.",
        "  - CASUAL CONVERSATION & CONFIRMATIONS: If the user asks a casual question or asks you to confirm something (e.g., 'OK, you understand my name right?'), you MUST provide a natural conversational reply in assistant_text (e.g., 'Yes, I got your name! What else can I help you with?').",
        "  - The assistant_text MUST NEVER BE EMPTY. Always provide a relevant, natural conversational reply.",
        "  - If you perform an action (navigate, add to cart, open cart, checkout), you MUST explicitly state what you are doing in your assistant_text (e.g. 'Opening your cart...', 'Taking you to Takeaway...', 'Adding 2 Idly...').",
        "  - If the user changes their mind (e.g. switches from takeaway to dine-in), acknowledge the change naturally and emit the appropriate ui_actions.",
        "",
        "══════════════════════════════════",
        "  RESPONSE FORMAT",
        "══════════════════════════════════",
    ])

    if not is_followup:
        lines.extend([
            "Return ONLY valid JSON with EXACTLY these keys:",
            '  {',
            '    "transcribed_user_text": "<what the user said — exact transcription>",',
            '    "tool_name": null,',
            '    "params": {},',
            '    "ui_actions": [ ... ],',
            '    "assistant_text": "<your conversational reply — MUST NEVER BE EMPTY>"',
            '  }',
            "",
            "tool_name MUST always be null. Never set it to anything else.",
            "",
            "Available ui_actions (frontend-only, no DB):",
            "  { action: set_customer, name: <str>, phone: <str> }",
            "     → Store customer name and/or phone in frontend state.",
            "  { action: set_flow_stage, stage: <STAGE_NAME> }",
            "     → Advance the journey to the next stage.",
            "  { action: set_order_type, type: 'dine-in' | 'takeaway' }",
            "     → Set the order mode.",
            "  { action: set_table_number, table: '<number>' }",
            "     → Record dine-in table number.",
            "  { action: navigate, page: 'dine-in' | 'take-away' | 'checkout' | 'payment' | 'home' }",
            "     → Navigate the customer to the specified page.",
            "  { action: add_to_cart, item: '<exact menu name>', quantity: <int> }",
            "     → Add an item to the cart. item must match a name from the AVAILABLE MENU above.",
            "  { action: remove_from_cart, item: '<exact menu name>' }",
            "     → Remove an item from the cart.",
            "  { action: view_cart }",
            "     → Open the cart sidebar.",
            "  { action: trigger_checkout }",
            "     → Navigate to checkout with customer details pre-filled.",
            "  { action: auto_navigate_to_payment, delay_ms: 2500 }",
            "     → Auto-navigate to payment after specified milliseconds.",
            "  { action: payment_method, method: 'Cash' | 'UPI' }",
            "     → Select payment method and confirm the order.",
            "  { action: start_order_tracking }",
            "     → Begin polling order status updates.",
            "  { action: request_feedback }",
            "     → Show the feedback screen.",
            "",
            'Example output:',
            '{"transcribed_user_text":"Two Onion Rava Dosa and one Vada","tool_name":null,"params":{},'
            '"ui_actions":[{"action":"add_to_cart","item":"Onion Rava Dosa","quantity":2},'
            '{"action":"add_to_cart","item":"Vada","quantity":1}],'
            '"assistant_text":"I\'ve added 2 Onion Rava Dosa and 1 Vada to your cart. Would you like anything else?"}',
        ])
    else:
        lines.append(
            'Return JSON with only the key "assistant_text" — a natural conversational reply.'
        )

    return "\n".join(lines)





def build_tool_prompt(user, is_voice: bool = False, is_followup: bool = False) -> str:
    lines = [
        "You are a restaurant voice assistant. You may receive text or an audio file from the user.",
        "If audio is provided, carefully transcribe and understand the user's spoken words. They may use regional accents, Thanglish, or Hinglish.",
    ]
    
    if not is_followup:
        lines.append("Return only valid JSON with the keys: transcribed_user_text, tool_name, params, assistant_text.")
        
    lines.extend([
        "CRITICAL INSTRUCTION: Adopt a natural, everyday spoken conversational tone (e.g., Spoken Tamil). Do NOT use highly formal, 'pure', or textbook translations (e.g., avoid written/literary Tamil). Keep it sounding like a normal human assistant, but avoid overly informal slang words like 'macha' or 'bhai'. For example, in Tamil, say 'சரி, ஆர்டர் செய்றேன்' instead of 'நான் ஆர்டர் செய்கிறேன்'.",
        "- You MUST strictly match the language of the user's MOST RECENT message. If the user speaks English, you MUST reply in English. Do NOT default to regional languages. Respond in regional languages (Tamil, Hindi, Thanglish, Hinglish) ONLY IF the user speaks them in their most recent message.",
        "- STRICT ENCODING RULE: ALWAYS use standard plain text characters. NEVER use bold, italics, markdown, emojis, mathematical alphanumeric symbols, or extended unicode blocks. Ensure your text contains absolutely NO markdown formatting.",
        "- For Tamil text, use ONLY standard Unicode U+0B80-U+0BFF. Do NOT use any Grantha, Brahmi, or special symbolic characters. Output pure, simple letters only.",
        "- If the user asks for ANY reports, metrics, or analytics (e.g. 'what is the total revenue', 'how many orders today', 'sales for noodles'), use the get_reports or get_item_sales_report tools. Calculate or summarize the data returned by these tools and present it to the user conversationally.",
    ])
    
    if not is_voice:
        lines.append("CRITICAL FONT RULE: Whenever you respond in a regional language or slang (like Tamil or Hindi), you MUST write the 'assistant_text' using the English (Latin) alphabet (i.e., use Thanglish or Hinglish). DO NOT use native scripts (like Tamil or Devanagari) because the frontend UI fonts do not support them.")
    else:
        lines.append("For voice interactions, if the user speaks in a regional language (like Tamil or Hindi), you MUST write the 'assistant_text' in that exact native script (e.g. Tamil letters) so our Text-to-Speech engine can pronounce it correctly. Do NOT write it in English letters for voice responses, use the native alphabet.")
        
    if not is_followup:
        lines.extend([
            "For 'transcribed_user_text', transcribe EXACTLY what the user said in the language and script they spoke. Do not translate it to English.",
            "If no tool is needed, set tool_name to null and provide assistant_text.",
            "CRITICAL: Even if you are calling a tool, you MUST provide a conversational `assistant_text` describing what you are doing (e.g. 'I am navigating to the menu.', 'I am logging you out.'). Do not leave `assistant_text` empty when calling an action tool.",
            "CONVERSATIONAL FLOW & PERSISTENCE: You must act like a human assistant having a continuous conversation. NEVER simply answer and stop. Always verify the results of your actions, ask clarifying questions if details are missing, and keep following up until the user's ultimate goal is fully completed.",
            "INTENT TRACKING (CRITICAL): If you are currently in the middle of a step-by-step data collection process for a specific tool (e.g., adding a manager), you MUST remember your ultimate goal. If the user answers your question with a detail (like 'Satish Kumar'), DO NOT ask them what they want to do. Assume their answer is meant to fill the missing parameter for the tool you were just discussing, and immediately ask for the next missing parameter.",
            "STEP-BY-STEP DATA COLLECTION (CRITICAL): When a tool requires multiple parameters (e.g. creating a manager), NEVER ask for all of them at once. CRITICAL WORKFLOW: As soon as the user provides the VERY FIRST required parameter (e.g. the Name), you MUST IMMEDIATELY invoke the creation tool (e.g. create_manager) using just that parameter. DO NOT WAIT for the rest. Once the record is created, ask for the next missing detail. When the user provides it, IMMEDIATELY invoke the UPDATE tool (e.g. update_manager) using the ID returned from the creation step. (NOTE: If the user provides a string name for a parameter that requires an integer ID, like restaurant_id, you must FIRST use the appropriate list tool, e.g. get_restaurants, to find the ID before calling the update tool). Repeat this loop until all details are filled. CRITICAL STATE TRACKING: DO NOT REPEAT QUESTIONS for parameters already provided in the Conversation History.",
            "MULTI-TURN TOOL CALLS (CRITICAL): If you asked the user for clarification (e.g. asking for a valid restaurant ID or missing field) in the previous turn, you MUST use their new answer combined with ALL previously gathered parameters from the Conversation History to finally execute the tool. DO NOT forget parameters the user already provided earlier in the chat. Extract them from the history block.",
            "If a user asks you to verify, check, or find something (e.g., 'are there any noodles?', 'check if X is in Y category'), you MUST use the appropriate tool (like search_menu_item) to get real data. DO NOT GUESS.",
            "If a tool call requires IDs (like category_id), you must FIRST use the appropriate list tool (like list_menu_categories) to find the ID before calling the update tool.",
            "DATABASE LANGUAGE RULE: All database records (menu items, categories, etc.) are stored in English. When you extract a name or search query from a regional language to pass as a tool parameter (e.g., passing 'name' to search_menu_item), you MUST strictly translate it to English first. DO NOT pass Tamil/Hindi text into tool parameters.",
            "HALLUCINATION PREVENTION: NEVER hallucinate data such as menu items, prices, orders, or statuses. ALWAYS use the appropriate tool (e.g., search_menu_item, list_menu_items, get_order_status) to fetch real data before confirming it exists."
        ])
    
    # Add role-specific context
    lines.append("CRITICAL RULE: In this system, 'hotel' and 'restaurant' are EXACTLY the same thing. Treat them as complete synonyms. If a user says 'hotel', they mean 'restaurant' and vice versa.")
    if user.role == "SUPER_ADMIN":
        lines.append("You are speaking with a SUPER_ADMIN who has full access to all hotels/restaurants and application features. You should assist them with any task across the entire system.")
        lines.append("When tools allow an optional restaurant_id parameter, you can provide it to filter, or omit it to fetch data for all restaurants/hotels.")
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

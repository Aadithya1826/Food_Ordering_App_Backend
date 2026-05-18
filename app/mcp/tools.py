from sqlalchemy.orm import Session
from fastapi import HTTPException

from ..models.menu import MenuCategory, MenuItem
from ..models.order import Order, OrderItem
from ..models.restaurant import Restaurant
from ..models.table import Table
from ..schemas.order import OrderCreate
from ..utils.roles import filter_by_user_restaurant, require_role, require_restaurant_access


def list_menu_items(db: Session, user, restaurant_id: int | None = None) -> list[dict]:
    query = db.query(MenuItem).filter(MenuItem.is_available == True)
    if restaurant_id is not None:
        query = query.filter(MenuItem.restaurant_id == restaurant_id)
    else:
        query = filter_by_user_restaurant(user, query)

    return [
        {
            "id": item.id,
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


def get_order_status(db: Session, user, order_id: int) -> dict:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
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
        "description": "Return the current status for an order.",
        "parameters": {
            "order_id": "Order ID to inspect.",
        },
        "handler": get_order_status,
    },
    "list_restaurants": {
        "description": "List all restaurants (SUPER_ADMIN only).",
        "parameters": {},
        "handler": list_restaurants,
    },
}


def build_tool_prompt() -> str:
    lines = [
        "You are a restaurant voice assistant. When the user asks for menu details, order creation, or order status, select the best tool to use.",
        "Return only valid JSON with the keys: tool_name, params, assistant_text.",
        "If no tool is needed, set tool_name to null and provide assistant_text.",
        "Available tools:",
    ]
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

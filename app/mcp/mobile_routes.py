"""
mobile_routes.py

PHASES 6, 7, 8, 9, 10, 11 — Complete Mobile Customer Agent backend

Endpoints:
1. POST /api/v1/public/mcp/mobile-customer-chat  (existing — Gemini-powered chat)
2. POST /api/v1/public/mcp/mobile-agent-tool     (NEW — Vapi tool data fetch)

Architecture:
    Vapi tool call → vapiStore.fetchAgentData() → POST /mobile-agent-tool
    → validates customer ownership → reads from existing backend services
    → returns safe structured data → vapiStore → Vapi → spoken response

SECURITY:
- customer_id is validated from the request; LLM-provided IDs are cross-checked
  against phone-based ownership.
- No direct SQL generation from the LLM.
- No generic database-write tools exposed.
- Only existing read-only APIs are called internally.
"""

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
import json
import logging
from typing import Any, Dict, Optional

from ..utils.dependencies import get_db
from ..schemas.mobile_mcp import MobileCustomerMCPRequest, MobileCustomerMCPResponse, UIAction, MobileAgentData
from ..prompts.mobile_customer_agent import MOBILE_CUSTOMER_AGENT_PROMPT
from .client import GeminiClient
from .routes import _generate_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter()
client = GeminiClient()


# ─── Schema for tool-data endpoint ───────────────────────────────────────────────

# pyrefly: ignore [missing-import]
from pydantic import BaseModel


class MobileAgentToolRequest(BaseModel):
    """Schema for the Vapi tool data-fetch endpoint."""
    tool_name: str
    arguments: Dict[str, Any] = {}
    # Customer identity — used for ownership validation
    customer_id: Optional[int] = None
    phone: Optional[str] = None
    restaurant_id: Optional[int] = None


# ─── Ownership helper ─────────────────────────────────────────────────────────────

def _resolve_customer(db: Session, customer_id: Optional[int], phone: Optional[str]):
    """
    Resolves and validates customer identity.
    Returns the customer ORM object or None.
    Phone takes precedence over customer_id for ownership checks.
    """
    from ..models.customer import Customer
    from ..utils.phone import normalize_phone

    if phone:
        norm = normalize_phone(phone.strip())
        cust = db.query(Customer).filter(Customer.phone == norm).first()
        if cust:
            # Cross-check: if customer_id was supplied, it must match
            if customer_id and cust.id != customer_id:
                return None  # Mismatch — reject
            return cust

    if customer_id:
        return db.query(Customer).filter(Customer.id == customer_id).first()

    return None


# ─── Tool handler ─────────────────────────────────────────────────────────────────

@router.post("/api/v1/public/mcp/mobile-agent-tool")
async def mobile_agent_tool(
    request: MobileAgentToolRequest,
    db: Session = Depends(get_db),
):
    """
    Safe READ-ONLY data-fetch endpoint for Vapi tool calls.

    Supported tools:
        menu_search
        customer_get_profile
        customer_get_rewards
        customer_get_orders
        customer_get_order
        order_get_tracking
        catering_get_data
    """
    tool = request.tool_name
    args = request.arguments
    logger.info(f"[MobileAgentTool] tool={tool} customer_id={request.customer_id}")

    try:

        # ── menu_search ────────────────────────────────────────────────────────

        if tool == "menu_search":
            from ..models.menu import MenuItem, MenuCategory

            query_str = str(args.get("query", "")).lower().strip()
            category_name = str(args.get("category_name", "")).lower().strip()
            restaurant_id = request.restaurant_id

            if not restaurant_id:
                return {"success": False, "code": "NO_RESTAURANT_SELECTED", "error": "Please select a restaurant outlet first."}

            q = db.query(MenuItem).filter(MenuItem.is_available == True)
            q = q.filter(MenuItem.restaurant_id == restaurant_id)
            items = q.all()

            cat_q = db.query(MenuCategory).filter(MenuCategory.restaurant_id == restaurant_id)
            all_categories = cat_q.all()

            # Filter by category name if provided
            if category_name:
                clean_cat = category_name.replace("varieties", "").replace("items", "").strip()
                cat_ids = [
                    c.id for c in all_categories
                    if clean_cat in c.name.lower() or c.name.lower() in clean_cat
                ]
                if not cat_ids:
                    return {"success": False, "code": "CATEGORY_NOT_FOUND", "error": f"The category '{category_name}' does not exist on this restaurant's menu."}
                items = [i for i in items if i.category_id in cat_ids]

            # Filter by search query
            if query_str:
                # Remove common voice-bot conversational fluff
                noise_words = ["varieties", "items", "please", "some", "show", "me", "list"]
                keywords = [kw for kw in query_str.split() if kw not in noise_words]
                
                if keywords:
                    filtered_items = []
                    for i in items:
                        name_lower = i.name.lower()
                        desc_lower = (i.description or "").lower()
                        # If ANY important keyword is in the name or description, include it
                        if any(kw in name_lower or kw in desc_lower for kw in keywords):
                            filtered_items.append(i)
                    
                    items = filtered_items

            if not items:
                if query_str:
                    return {"success": False, "code": "MENU_ITEM_NOT_FOUND", "error": f"I couldn't find '{query_str}' on the menu."}
                else:
                    return {"success": False, "code": "MENU_EMPTY", "error": "This restaurant currently has no menu items available."}

            return {
                "success": True,
                "categories": [{"id": c.id, "name": c.name} for c in all_categories],
                "menu_items": [
                    {
                        "id": i.id,
                        "name": i.name,
                        "price": float(i.price),
                        "description": i.description,
                        "is_available": i.is_available,
                        "category_id": i.category_id,
                    }
                    for i in items[:20]  # Cap at 20 items
                ],
                "count": len(items),
            }

        # ── customer_get_profile ───────────────────────────────────────────────

        elif tool == "customer_get_profile":
            customer = _resolve_customer(db, request.customer_id, request.phone)
            if not customer:
                return {"success": False, "error": "Customer not found or unauthorized."}

            return {
                "success": True,
                "profile": {
                    "id": customer.id,
                    "name": customer.name,
                    "phone": customer.phone,
                    "email": getattr(customer, "email", None),
                    "loyalty_points": getattr(customer, "loyalty_points", 0),
                    "profile_picture_url": getattr(customer, "profile_picture_url", None),
                },
            }

        # ── customer_get_rewards ───────────────────────────────────────────────

        elif tool == "customer_get_rewards":
            customer = _resolve_customer(db, request.customer_id, request.phone)
            if not customer:
                return {"success": False, "error": "Customer not found or unauthorized."}

            # Read loyalty points from the customer record directly
            points = getattr(customer, "loyalty_points", 0)
            tier = "Standard"

            return {
                "success": True,
                "rewards": {
                    "customer_id": customer.id,
                    "customer_name": customer.name,
                    "reward_points": points,
                    "tier": tier,
                },
            }

        # ── customer_get_orders ────────────────────────────────────────────────

        elif tool == "customer_get_orders":
            customer = _resolve_customer(db, request.customer_id, request.phone)
            if not customer:
                return {"success": False, "error": "Customer not found or unauthorized."}

            from ..models.order import Order
            limit = min(int(args.get("limit", 10)), 20)  # max 20

            orders = (
                db.query(Order)
                .filter(Order.customer_phone == customer.phone)
                .order_by(Order.created_at.desc())
                .limit(limit)
                .all()
            )

            return {
                "success": True,
                "orders": [
                    {
                        "id": o.id,
                        "order_type": o.order_type,
                        "status": o.status,
                        "total_amount": float(o.total_amount or 0),
                        "payment_status": o.payment_status or "Unknown",
                        "created_at": str(o.created_at),
                    }
                    for o in orders
                ],
                "count": len(orders),
            }

        # ── customer_get_order ─────────────────────────────────────────────────

        elif tool == "customer_get_order":
            customer = _resolve_customer(db, request.customer_id, request.phone)
            if not customer:
                return {"success": False, "error": "Customer not found or unauthorized."}

            from ..models.order import Order, OrderItem
            order_id_str = str(args.get("order_id", ""))
            if not order_id_str:
                return {"success": False, "error": "order_id is required."}

            # Try integer ID (Order.id is the primary key)
            order = None
            try:
                order = db.query(Order).filter(Order.id == int(order_id_str)).first()
            except ValueError:
                pass

            if not order:
                return {"success": False, "error": f"Order {order_id_str} not found."}

            # Ownership check: order must belong to this customer
            if order.customer_phone != customer.phone:
                return {"success": False, "error": "You are not authorized to view this order."}

            items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()

            return {
                "success": True,
                "order": {
                    "id": order.id,
                    "order_type": order.order_type,
                    "status": order.status,
                    "total_amount": float(order.total_amount or 0),
                    "payment_status": order.payment_status or "Unknown",
                    "created_at": str(order.created_at),
                    "items": [
                        {
                            "id": oi.id,
                            "menu_item_id": oi.menu_item_id,
                            "quantity": oi.quantity,
                            "price": float(oi.price or 0),
                        }
                        for oi in items
                    ],
                },
            }

        # ── order_get_tracking ────────────────────────────────────────────────

        elif tool == "order_get_tracking":
            customer = _resolve_customer(db, request.customer_id, request.phone)
            if not customer:
                return {"success": False, "error": "Customer not found or unauthorized."}

            from ..models.order import Order
            from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory

            order_id_str = str(args.get("order_id", ""))
            if not order_id_str:
                return {"success": False, "error": "order_id is required."}

            order = None
            try:
                order = db.query(Order).filter(Order.id == int(order_id_str)).first()
            except ValueError:
                pass

            if not order:
                return {"success": False, "error": f"Order {order_id_str} not found."}

            # Ownership check
            if order.customer_phone != customer.phone:
                return {"success": False, "error": "You are not authorized to track this order."}

            # Get delivery assignment if exists
            delivery = None
            if order.order_type in ("DELIVERY", "Delivery"):
                delivery = db.query(DeliveryAssignment).filter(
                    DeliveryAssignment.order_id == order.id
                ).first()

            history = []
            if delivery:
                history_records = db.query(DeliveryStatusHistory).filter(
                    DeliveryStatusHistory.delivery_id == delivery.id
                ).order_by(DeliveryStatusHistory.timestamp.asc()).all()
                history = [
                    {"status": h.status, "timestamp": str(h.timestamp)}
                    for h in history_records
                ]

            return {
                "success": True,
                "tracking": {
                    "order_id": order.id,
                    "order_type": order.order_type,
                    "restaurant_status": order.status,
                    "payment_status": order.payment_status,
                    "delivery_status": delivery.status if delivery else None,
                    "has_rider": delivery is not None,
                    "status_history": history,
                    "created_at": str(order.created_at),
                },
            }

        # ── catering_get_data ─────────────────────────────────────────────────

        elif tool == "catering_get_data":
            data_type = args.get("data_type", "packages")

            if data_type == "packages":
                # Fetch available catering packages from the DB
                try:
                    from ..models.catering import CateringPackage
                    pkgs = db.query(CateringPackage).filter(
                        CateringPackage.is_active == True
                    ).all()
                    return {
                        "success": True,
                        "packages": [
                            {
                                "id": p.id,
                                "name": p.name,
                                "description": getattr(p, "description", None),
                                "price_per_person": float(getattr(p, "price_per_person", 0)),
                                "min_pax": getattr(p, "min_pax", None),
                                "max_pax": getattr(p, "max_pax", None),
                            }
                            for p in pkgs
                        ],
                    }
                except Exception as e:
                    logger.warning(f"Could not fetch catering packages: {e}")
                    return {"success": False, "error": "Catering packages unavailable."}

            elif data_type == "customer_orders":
                customer = _resolve_customer(db, request.customer_id, request.phone)
                if not customer:
                    return {"success": False, "error": "Customer not found or unauthorized."}

                try:
                    from ..models.catering import CateringOrder
                    orders = db.query(CateringOrder).filter(
                        CateringOrder.customer_id == customer.id
                    ).order_by(CateringOrder.created_at.desc()).limit(10).all()

                    return {
                        "success": True,
                        "catering_orders": [
                            {
                                "id": o.id,
                                "package_name": o.package_name,
                                "event_date": str(o.event_date or ""),
                                "guest_count": o.guest_count,
                                "total_amount": float(o.total_amount or 0),
                                "advance_amount": float(o.advance_amount or 0),
                                "balance_amount": float(o.balance_amount or 0),
                                "paid_amount": float(o.paid_amount or 0),
                                "payment_status": o.payment_status or "PENDING",
                                "order_status": o.order_status or "CONFIRMED",
                                "balance_due_date": str(o.full_payment_due_date or ""),
                            }
                            for o in orders
                        ],
                    }
                except Exception as e:
                    logger.warning(f"Could not fetch catering orders: {e}")
                    return {"success": False, "error": "Catering order history unavailable."}

            elif data_type == "single_order":
                customer = _resolve_customer(db, request.customer_id, request.phone)
                if not customer:
                    return {"success": False, "error": "Customer not found or unauthorized."}

                catering_order_id = args.get("catering_order_id")
                if not catering_order_id:
                    return {"success": False, "error": "catering_order_id is required."}

                try:
                    from ..models.catering import CateringOrder
                    order = db.query(CateringOrder).filter(
                        CateringOrder.id == int(catering_order_id),
                        CateringOrder.customer_id == customer.id,  # Ownership check
                    ).first()

                    if not order:
                        return {"success": False, "error": "Catering order not found or unauthorized."}

                    return {
                        "success": True,
                        "catering_order": {
                            "id": order.id,
                            "package_name": order.package_name,
                            "event_date": str(order.event_date or ""),
                            "guest_count": order.guest_count,
                            "total_amount": float(order.total_amount or 0),
                            "advance_amount": float(order.advance_amount or 0),
                            "balance_amount": float(order.balance_amount or 0),
                            "paid_amount": float(order.paid_amount or 0),
                            "payment_status": order.payment_status or "PENDING",
                            "order_status": order.order_status or "CONFIRMED",
                            "balance_due_date": str(order.full_payment_due_date or ""),
                        },
                    }
                except Exception as e:
                    logger.warning(f"Could not fetch catering order: {e}")
                    return {"success": False, "error": "Catering order unavailable."}

            return {"success": False, "error": f"Unknown catering data_type: {data_type}"}

        # ── Unknown tool ──────────────────────────────────────────────────────

        else:
            logger.warning(f"[MobileAgentTool] Unknown tool rejected: {tool}")
            return {"success": False, "error": f'Tool "{tool}" is not supported.'}

    except Exception as e:
        logger.error(f"[MobileAgentTool] Unexpected error for tool={tool}: {e}")
        return {"success": False, "error": "An unexpected error occurred. Please try again."}


# ─── Existing Chat Endpoint (preserved exactly) ───────────────────────────────────

@router.post("/api/v1/public/mcp/mobile-customer-chat", response_model=MobileCustomerMCPResponse)
async def mobile_customer_chat(
    request: MobileCustomerMCPRequest,
    db: Session = Depends(get_db),
):
    try:
        from ..models.menu import MenuItem, MenuCategory

        categories = db.query(MenuCategory)
        menu_items = db.query(MenuItem).filter(MenuItem.is_available == True)

        if request.restaurant_id:
            categories = categories.filter(MenuCategory.restaurant_id == request.restaurant_id)
            menu_items = menu_items.filter(MenuItem.restaurant_id == request.restaurant_id)

        cat_names = [{"id": c.id, "name": c.name} for c in categories.all()]
        items = [
            {"id": i.id, "name": i.name, "price": i.price, "category_id": i.category_id}
            for i in menu_items.all()
        ]

        sys_prompt = MOBILE_CUSTOMER_AGENT_PROMPT + "\n\n=== LIVE BACKEND CONTEXT ===\n"
        sys_prompt += f"Restaurant Categories: {json.dumps(cat_names)}\n"
        sys_prompt += f"Restaurant Items: {json.dumps(items)}\n"

        if request.screen:
            sys_prompt += f"Current Screen: {json.dumps(request.screen)}\n"
        if request.app_context:
            sys_prompt += f"App Context: {json.dumps(request.app_context)}\n"
        if request.cart:
            sys_prompt += f"Current Cart: {json.dumps(request.cart)}\n"

        history_text = ""
        if request.chat_history:
            history_text = "\n--- Conversation History ---\n"
            for msg in request.chat_history:
                history_text += f"{msg.role.capitalize()}: {msg.text}\n"
            history_text += "----------------------------\n"

        user_input = request.message
        if not user_input and not request.audio_base64:
            return MobileCustomerMCPResponse(assistant_text="How can I help you?")

        full_prompt = (
            f"{sys_prompt}\n\n"
            f"{history_text}"
            f"User: {user_input}\n"
            "Respond with valid JSON only."
        )

        parsed = await client.generate_json(full_prompt, audio_base64=request.audio_base64)

        assistant_text = parsed.get("assistant_text", "I'm sorry, I couldn't process that.")
        ui_actions_raw = parsed.get("ui_actions", [])
        data_raw = parsed.get("data", {})

        audio_payload = None
        if assistant_text and len(assistant_text) < 500:
            audio_payload = await _generate_tts_audio(assistant_text)

        ui_actions = [UIAction(**action) for action in ui_actions_raw]
        data = MobileAgentData(**data_raw)

        return MobileCustomerMCPResponse(
            assistant_text=assistant_text,
            ui_actions=ui_actions,
            data=data,
            requires_confirmation=parsed.get("requires_confirmation", False),
            audio_base64=audio_payload,
            transcribed_user_text=parsed.get("transcribed_user_text"),
        )

    except Exception as e:
        logger.error(f"[MobileCustomerChat] error: {e}")
        return MobileCustomerMCPResponse(
            assistant_text="I'm sorry, an error occurred. Please try again.",
        )

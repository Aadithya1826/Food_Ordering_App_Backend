# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
import json as _json

from ..utils.dependencies import get_db
from .client import GeminiClient
from .routes import _generate_tts_audio
from .schemas import CustomerMCPRequest, MCPVoiceResponse
from .tools import build_customer_tool_prompt, CUSTOMER_TOOL_REGISTRY

router = APIRouter()
client = GeminiClient()

@router.post("/api/v1/public/mcp/customer-chat", response_model=MCPVoiceResponse)
async def customer_chat(
    request: CustomerMCPRequest,
    db: Session = Depends(get_db),
):
    """
    Public (no-auth) endpoint for the customer UI chatbot.
    Supports text and audio (MediaRecorder audio/webm) input.
    Returns assistant_text + optional base64 gTTS audio payload.
    """
    # Fetch menu to inject into prompt to prevent hallucination
    from ..models.menu import MenuItem, MenuCategory
    categories = db.query(MenuCategory)
    menu_items = db.query(MenuItem).filter(MenuItem.is_available == True)
    if request.restaurant_id:
        categories = categories.filter(MenuCategory.restaurant_id == request.restaurant_id)
        menu_items = menu_items.filter(MenuItem.restaurant_id == request.restaurant_id)
    
    cat_names = [c.name for c in categories.all()]
    cat_text = "Categories available: " + ", ".join(cat_names) if cat_names else "No categories found."
    items_text = ", ".join([f"{item.name} (₹{item.price})" for item in menu_items.all()])
    
    if not items_text and not cat_names:
        menu_text = "No items or categories available."
    else:
        menu_text = f"{cat_text}\n\nItems available: {items_text}"

    prompt = build_customer_tool_prompt(
        is_voice=request.is_voice, 
        menu_text=menu_text, 
        order_id=request.order_id, 
        current_page=request.current_page,
        order_type=request.order_type,
        cart_data=request.cart_data,
        customer_name=request.customer_name,
        customer_phone=request.customer_phone,
        flow_stage=request.flow_stage,
        table_number=request.table_number,
        payment_status=request.payment_status,
        order_status=request.order_status,
        detected_language=request.detected_language,
        session_id=request.session_id,
    )

    history_text = ""
    if request.chat_history:
        history_text = "\n--- Conversation History ---\n"
        for msg in request.chat_history:
            role = "Assistant" if msg.role == "assistant" else "User"
            history_text += f"{role}: {msg.text}\n"
        history_text += "----------------------------\n"

    full_prompt = (
        f"{prompt}\n\n"
        f"{history_text}"
        f"User: {request.prompt}\n"
        "Respond with valid JSON only."
    )

    # --- Call Gemini (with optional audio) ---
    try:
        parsed = await client.generate_json(
            full_prompt,
            audio_base64=request.audio_base64
        )
    except Exception as e:
        print(f"[CustomerChat] Gemini error: {e}")
        error_msg = str(e)
        if "429" in error_msg:
            fallback = "I'm sorry, the AI service is busy right now. Please try again in a moment."
        elif "503" in error_msg:
            fallback = "I'm sorry, the AI service is temporarily unavailable. Please try again later."
        else:
            fallback = "Sorry, I couldn't understand that. Could you please try again?"
        return MCPVoiceResponse(
            assistant_text=fallback,
            tool_name=None,
            tool_result=None,
            audio_payload=await _generate_tts_audio(fallback) if request.is_voice else None
        )

    tool_name = parsed.get("tool_name")
    
    assistant_text = parsed.get("assistant_text", "")
    if isinstance(assistant_text, str):
        assistant_text = assistant_text.strip()
    else:
        assistant_text = ""
        
    transcribed_user_text = parsed.get("transcribed_user_text", None)
    params = parsed.get("params", {}) or {}
    ui_actions = parsed.get("ui_actions") or []

    # --- Execute tool if requested ---
    tool_result = None
    if tool_name and tool_name in CUSTOMER_TOOL_REGISTRY:
        try:
            handler = CUSTOMER_TOOL_REGISTRY[tool_name]["handler"]
            # Public handlers take (db, **params) — no user object
            tool_result = handler(db, **params)

            # --- Second pass: summarize tool result in natural language ---
            followup_prompt = (
                f"{build_customer_tool_prompt(is_voice=request.is_voice, is_followup=True, menu_text=menu_text, order_id=request.order_id, current_page=request.current_page, order_type=request.order_type, cart_data=request.cart_data, customer_name=request.customer_name, customer_phone=request.customer_phone)}\n\n"
                f"The customer asked: {transcribed_user_text or request.prompt}\n"
                f"You used the tool '{tool_name}' and got this result:\n{tool_result}\n\n"
                "Give a natural, conversational answer summarizing this data. "
                "Return JSON with ONLY the key: 'assistant_text'."
            )
            try:
                second = await client.generate_json(followup_prompt)
                if "assistant_text" in second:
                    assistant_text = second["assistant_text"]
            except Exception as e2:
                print(f"[CustomerChat] Follow-up error: {e2}")

        except Exception as tool_err:
            print(f"[CustomerChat] Tool execution error: {tool_err}")
            assistant_text = assistant_text or "I couldn't retrieve that information right now. Please try again."

    if assistant_text:
        final_text = assistant_text
    else:
        action_types = [a.get("action") for a in ui_actions if isinstance(a, dict)]
        if "add_to_cart" in action_types:
            final_text = "Okay, I've updated your order. Anything else?"
        elif "navigate" in action_types:
            final_text = "Sure, taking you there now."
        elif "view_cart" in action_types:
            final_text = "Here is your cart."
        elif "trigger_checkout" in action_types:
            final_text = "Let me show you your order."
        elif "set_customer" in action_types:
            final_text = "Got it, I've updated your details. What's next?"
        elif "set_table_number" in action_types:
            final_text = "Table number confirmed."
        elif "set_order_type" in action_types:
            final_text = "Got it."
        elif "set_flow_stage" in action_types:
            final_text = "Okay, let's proceed."
        elif action_types:
            final_text = "Okay, got it."
        else:
            final_text = "I'm sorry, I didn't quite catch that. How can I help you today?"

    return MCPVoiceResponse(
        assistant_text=final_text,
        transcribed_user_text=transcribed_user_text,
        tool_name=None,
        tool_result=tool_result,
        parameters=params,
        ui_actions=ui_actions,
        audio_payload=await _generate_tts_audio(final_text, request.detected_language) if request.is_voice else None,
    )

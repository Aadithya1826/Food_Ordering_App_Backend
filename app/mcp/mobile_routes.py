from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
import json
import base64
import logging

from ..utils.dependencies import get_db
from ..schemas.mobile_mcp import MobileCustomerMCPRequest, MobileCustomerMCPResponse, UIAction, MobileAgentData
from ..prompts.mobile_customer_agent import MOBILE_CUSTOMER_AGENT_PROMPT
from .client import GeminiClient
from .routes import _generate_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter()
client = GeminiClient()


@router.post("/api/v1/public/mcp/mobile-customer-chat", response_model=MobileCustomerMCPResponse)
async def mobile_customer_chat(
    request: MobileCustomerMCPRequest,
    db: Session = Depends(get_db),
):
    try:
        # Fetch essential data minimally
        from ..models.menu import MenuItem, MenuCategory
        from ..models.orders import Order
        
        categories = db.query(MenuCategory)
        menu_items = db.query(MenuItem).filter(MenuItem.is_available == True)
        
        if request.restaurant_id:
            categories = categories.filter(MenuCategory.restaurant_id == request.restaurant_id)
            menu_items = menu_items.filter(MenuItem.restaurant_id == request.restaurant_id)
            
        cat_names = [{"id": c.id, "name": c.name} for c in categories.all()]
        items = [{"id": i.id, "name": i.name, "price": i.price, "category_id": i.category_id} for i in menu_items.all()]

        # System context injection
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
            return MobileCustomerMCPResponse(
                assistant_text="How can I help you?",
            )

        full_prompt = (
            f"{sys_prompt}\n\n"
            f"{history_text}"
            f"User: {user_input}\n"
            "Respond with valid JSON only."
        )

        parsed = await client.generate_json(
            full_prompt,
            audio_base64=request.audio_base64
        )

        assistant_text = parsed.get("assistant_text", "I'm sorry, I couldn't process that.")
        ui_actions_raw = parsed.get("ui_actions", [])
        data_raw = parsed.get("data", {})
        
        # Audio generation if needed (assuming user spoke or explicitly needs voice, let's always generate for simplicity or check if we received voice)
        # Actually, for hands-free it's nice to always have the audio URL/base64 unless it's just a UI click
        audio_payload = None
        if assistant_text and len(assistant_text) < 500:  # Prevent huge TTS
            audio_payload = await _generate_tts_audio(assistant_text)

        ui_actions = [UIAction(**action) for action in ui_actions_raw]
        data = MobileAgentData(**data_raw)

        return MobileCustomerMCPResponse(
            assistant_text=assistant_text,
            ui_actions=ui_actions,
            data=data,
            requires_confirmation=parsed.get("requires_confirmation", False),
            audio_base64=audio_payload,
            transcribed_user_text=parsed.get("transcribed_user_text") # Gemini API might return transcription if we ask
        )

    except Exception as e:
        logger.error(f"[MobileCustomerChat] error: {e}")
        return MobileCustomerMCPResponse(
            assistant_text="I'm sorry, an error occurred. Please try again.",
        )

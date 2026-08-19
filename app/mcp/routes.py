import re
import base64
import io
import edge_tts
import json
from gtts import gTTS
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..utils.dependencies import get_current_user, get_db
from .client import GeminiClient
from .schemas import (
    MCPResponse,
    MCPTextRequest,
    MCPToolRequest,
    MCPVoiceRequest,
    MCPTtsRequest,
    MCPVoiceResponse,
    CustomerMCPRequest,
)
from .tools import (
    build_tool_prompt,
    build_customer_tool_prompt,
    execute_tool,
    list_tool_definitions,
    CUSTOMER_TOOL_REGISTRY,
)

router = APIRouter()
client = GeminiClient()


@router.get("/api/v1/mcp/tools", response_model=list[dict])
def list_tools():
    return list_tool_definitions()


@router.post("/api/v1/mcp/tools/execute", response_model=MCPResponse)
def execute_tool_route(
    request: MCPToolRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = execute_tool(db, user, request.tool_name, request.parameters)
    return MCPResponse(
        assistant_text=f"Executed tool {request.tool_name}",
        tool_name=request.tool_name,
        tool_result=result,
    )


@router.post("/api/v1/mcp/natural-language", response_model=MCPResponse)
async def natural_language_query(
    request: MCPTextRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = build_tool_prompt(user, is_voice=request.is_voice)
    
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

    try:
        parsed = await client.generate_json(full_prompt, audio_base64=request.audio_base64)
    except Exception as e:
        print(f"Error parsing JSON from Gemini: {e}")
        try:
            raw_text = await client.generate_text(full_prompt, audio_base64=request.audio_base64)
            parsed = client._try_parse_json(raw_text)
            if not parsed:
                return MCPResponse(assistant_text=raw_text, tool_name=None, tool_result=None)
        except Exception as e2:
            print(f"Error falling back to text generation: {e2}")
            error_msg = str(e2)
            if "429" in error_msg:
                user_msg = "I'm sorry, my AI service is currently rate-limited (Too Many Requests). Please wait a moment and try again."
            elif "503" in error_msg:
                user_msg = "I'm sorry, my AI service is temporarily unavailable. Please try again later."
            else:
                user_msg = f"I'm sorry, I encountered an AI connection error: {error_msg}"
                
            return MCPResponse(
                assistant_text=user_msg,
                tool_name=None,
                tool_result=None
            )

    tool_name = parsed.get("tool_name")
    assistant_text = parsed.get("assistant_text", "")
    transcribed_user_text = parsed.get("transcribed_user_text", None)
    params = parsed.get("params", {})

    if tool_name:
        try:
            result = execute_tool(db, user, tool_name, params)
            
            # --- Second Pass: Ask Gemini to summarize the tool result ---
            final_assistant_text = assistant_text or f"Invoked tool {tool_name}"
            actual_user_text = transcribed_user_text if transcribed_user_text else request.prompt
            
            clean_instructions = build_tool_prompt(user, is_voice=request.is_voice, is_followup=True)
            
            followup_prompt = (
                f"{clean_instructions}\n\n"
                f"The user said: {actual_user_text}\n"
                f"You used the tool '{tool_name}' which returned this result:\n{result}\n\n"
                "Provide a natural, conversational response to the user summarizing this data. "
                "CRITICAL: Follow the exact same slang, dialect, and font rules as instructed above. "
                "Respond with valid JSON containing ONLY the key: 'assistant_text'."
            )
            try:
                second_parsed = await client.generate_json(followup_prompt)
                if "assistant_text" in second_parsed:
                    final_assistant_text = second_parsed["assistant_text"]
            except Exception as e:
                print(f"Error generating follow-up response: {e}")
            # -----------------------------------------------------------

            return MCPResponse(
                assistant_text=final_assistant_text,
                transcribed_user_text=transcribed_user_text,
                tool_name=tool_name,
                tool_result=result,
            )
        except HTTPException as tool_err:
            return MCPResponse(
                assistant_text=f"I couldn't complete that action: {tool_err.detail}",
                transcribed_user_text=transcribed_user_text,
                tool_name=None,
                tool_result=None,
            )
        except Exception as tool_err:
            return MCPResponse(
                assistant_text=f"An unexpected error occurred while running the tool: {str(tool_err)}",
                transcribed_user_text=transcribed_user_text,
                tool_name=None,
                tool_result=None,
            )

    return MCPResponse(
        assistant_text=assistant_text or "I did not detect a tool action.", 
        transcribed_user_text=transcribed_user_text,
        tool_name=None, 
        tool_result=None
    )


@router.post("/api/v1/mcp/voice/ask", response_model=MCPVoiceResponse)
async def voice_assistant_query(
    request: MCPVoiceRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    text_request = MCPTextRequest(
        prompt=request.transcribed_text, 
        restaurant_id=request.restaurant_id,
        chat_history=request.chat_history,
        audio_base64=request.audio_base64,
        is_voice=request.is_voice
    )
    response = await natural_language_query(text_request, user=user, db=db)
    
    # Generate high-quality TTS audio for regional languages using gTTS
    audio_base64 = None
    assistant_text = response.assistant_text
    if assistant_text:
        has_tamil = bool(re.search(r'[\u0B80-\u0BFF]', assistant_text))
        has_hindi = bool(re.search(r'[\u0900-\u097F]', assistant_text))
        lang = 'ta' if has_tamil else ('hi' if has_hindi else 'en')
        
        try:
            # We use gTTS to generate the audio in-memory
            tts = gTTS(text=assistant_text, lang=lang, slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            audio_base64 = base64.b64encode(fp.read()).decode('utf-8')
        except Exception as e:
            print(f"Error generating gTTS audio: {e}")

    return MCPVoiceResponse(
        assistant_text=response.assistant_text,
        transcribed_user_text=response.transcribed_user_text,
        tool_name=response.tool_name,
        tool_result=response.tool_result,
        audio_payload=audio_base64,
    )


@router.post("/api/v1/mcp/voice/tts", response_model=MCPVoiceResponse)
async def synthesize_voice(
    request: MCPTtsRequest,
    user=Depends(get_current_user),
):
    try:
        audio_body = await client.generate_speech(request.text, audio_encoding=request.audio_encoding)
        return MCPVoiceResponse(
            assistant_text=request.text,
            tool_name=None,
            tool_result=None,
            audio_payload=audio_body,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS generation failed: {exc}")


# ── Public Customer Chatbot Endpoint (no auth) ──────────────────────────────

async def _generate_tts_audio(text: str, lang_hint: str = None) -> str | None:
    """Detect language from text (and optional hint), generate edge-tts audio, return base64 MP3."""
    if not text:
        return None
    # Check Unicode script ranges for automatic detection
    has_tamil     = bool(re.search(r'[\u0B80-\u0BFF]', text))
    has_devanag   = bool(re.search(r'[\u0900-\u097F]', text))  # Hindi, Marathi, Bhojpuri
    has_malayalam = bool(re.search(r'[\u0D00-\u0D7F]', text))
    has_kannada   = bool(re.search(r'[\u0C80-\u0CFF]', text))
    has_telugu    = bool(re.search(r'[\u0C00-\u0C7F]', text))
    has_urdu      = bool(re.search(r'[\u0600-\u06FF]', text))
    has_bengali   = bool(re.search(r'[\u0980-\u09FF]', text))
    has_gujarati  = bool(re.search(r'[\u0A80-\u0AFF]', text))
    has_gurmukhi  = bool(re.search(r'[\u0A00-\u0A7F]', text))

    if has_tamil:       voice = 'ta-IN-ValluvarNeural'
    elif has_malayalam: voice = 'ml-IN-MidhunNeural'
    elif has_kannada:   voice = 'kn-IN-GaganNeural'
    elif has_telugu:    voice = 'te-IN-MohanNeural'
    elif has_urdu:      voice = 'ur-IN-SalmanNeural'
    elif has_bengali:   voice = 'bn-IN-BashkarNeural'
    elif has_gujarati:  voice = 'gu-IN-NiranjanNeural'
    elif has_gurmukhi:  voice = 'pa-IN-OjasNeural'
    elif has_devanag:   voice = 'hi-IN-MadhurNeural'
    else:
        # Fallback to English Male or hint-based
        hint = (lang_hint or '').lower()
        if 'tamil' in hint or 'tanglish' in hint: voice = 'ta-IN-ValluvarNeural'
        elif 'malayalam' in hint: voice = 'ml-IN-MidhunNeural'
        elif 'kannada' in hint: voice = 'kn-IN-GaganNeural'
        elif 'telugu' in hint: voice = 'te-IN-MohanNeural'
        elif 'hindi' in hint or 'hinglish' in hint or 'marathi' in hint or 'bhojpuri' in hint: voice = 'hi-IN-MadhurNeural'
        elif 'urdu' in hint: voice = 'ur-IN-SalmanNeural'
        elif 'bengali' in hint: voice = 'bn-IN-BashkarNeural'
        elif 'gujarati' in hint: voice = 'gu-IN-NiranjanNeural'
        elif 'punjabi' in hint: voice = 'pa-IN-OjasNeural'
        else: voice = 'en-IN-PrabhatNeural' # Indian English Male

    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return base64.b64encode(audio_data).decode('utf-8')
    except Exception as e:
        print(f"[Edge-TTS] Error generating audio (voice={voice}): {e}")
        # Fallback to English Male if selected language fails
        if voice != 'en-IN-PrabhatNeural':
            try:
                communicate = edge_tts.Communicate(text, 'en-IN-PrabhatNeural')
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]
                return base64.b64encode(audio_data).decode('utf-8')
            except Exception:
                pass
        return None


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
    
    # Debug log
    try:
        import json
        with open(r"C:\Users\solai\.gemini\antigravity-ide\brain\45a5a374-3741-46da-816b-0c0dbece4cef\scratch\parsed.log", "a") as f:
            f.write(f"PROMPT: {request.prompt}\nPARSED: {json.dumps(parsed)}\n\n")
    except Exception as e:
        print("Logging error:", e)

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
        tool_name=None,           # chatbot never calls DB tools
        tool_result=None,
        parameters=params,
        ui_actions=ui_actions,
        audio_payload=await _generate_tts_audio(final_text, request.detected_language) if request.is_voice else None,
    )

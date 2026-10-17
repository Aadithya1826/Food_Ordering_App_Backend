# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import StreamingResponse
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
import json as _json
import re as _re

from ..utils.dependencies import get_db
from ..services.menu_matcher import resolve_cart_actions
from .client import GeminiClient
from .routes import _generate_tts_audio, _SENTENCE_BOUNDARY, _MIN_TTS_CHARS
from .schemas import (
    CustomerMCPRequest,
    MCPVoiceResponse,
    CUSTOMER_RESPONSE_SCHEMA,
    FOLLOWUP_RESPONSE_SCHEMA,
)
from .tools import build_customer_tool_prompt, CUSTOMER_TOOL_REGISTRY

router = APIRouter()


def _build_chat_context(request: CustomerMCPRequest, db: Session):
    """Assemble the menu snapshot and the full prompt for one customer turn.

    Shared by the buffered and streaming endpoints so the two can never drift
    apart in prompt content or menu grounding.
    """
    # Fetch menu to inject into prompt to prevent hallucination
    from ..models.menu import MenuItem, MenuCategory
    categories = db.query(MenuCategory)
    menu_items = db.query(MenuItem).filter(MenuItem.is_available == True)
    if request.restaurant_id:
        categories = categories.filter(MenuCategory.restaurant_id == request.restaurant_id)
        menu_items = menu_items.filter(MenuItem.restaurant_id == request.restaurant_id)

    cat_names = [c.name for c in categories.all()]
    menu_rows = menu_items.all()
    menu_names = [item.name for item in menu_rows]

    cat_text = "Categories available: " + ", ".join(cat_names) if cat_names else "No categories found."
    items_text = ", ".join([f"{item.name} (₹{item.price})" for item in menu_rows])

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

    return full_prompt, menu_text, menu_names


def _fallback_text_for(ui_actions) -> str:
    """Canned reply when the model returned actions but no spoken text."""
    action_types = [a.get("action") for a in ui_actions if isinstance(a, dict)]
    if "add_to_cart" in action_types:
        return "Okay, I've updated your order. Anything else?"
    if "navigate" in action_types:
        return "Sure, taking you there now."
    if "view_cart" in action_types:
        return "Here is your cart."
    if "trigger_checkout" in action_types:
        return "Let me show you your order."
    if "set_customer" in action_types:
        return "Got it, I've updated your details. What's next?"
    if "set_table_number" in action_types:
        return "Table number confirmed."
    if "set_order_type" in action_types:
        return "Got it."
    if "set_flow_stage" in action_types:
        return "Okay, let's proceed."
    if action_types:
        return "Okay, got it."
    return "I'm sorry, I didn't quite catch that. How can I help you today?"


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
    full_prompt, menu_text, menu_names = _build_chat_context(request, db)

    # Select model family by turn type (voice vs text) via env vars.
    client = GeminiClient(mode="voice" if request.is_voice else "chat")

    # --- Call Gemini (with optional audio) ---
    try:
        parsed = await client.generate_json(
            full_prompt,
            audio_base64=request.audio_base64,
            audio_mime_type=request.audio_mime_type,
            response_schema=CUSTOMER_RESPONSE_SCHEMA,
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

    # --- Reconcile spoken item names against the real menu ---
    # The model is asked to emit exact menu names, but speech-to-text noise means
    # it regularly emits something close-but-wrong. An unmatched name reaches the
    # frontend as a phantom add_to_cart: the agent says "Added 2 Masala Dosa"
    # while the cart stays empty. Resolve here so speech and cart always agree.
    ui_actions, unresolved_items = resolve_cart_actions(ui_actions, menu_names)

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
                second = await client.generate_json(
                    followup_prompt,
                    response_schema=FOLLOWUP_RESPONSE_SCHEMA,
                    # This pass returns a single spoken sentence. The old 1500
                    # token budget let the model ramble, and every extra token
                    # is dead air before the customer hears anything.
                    max_tokens=256,
                )
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
        final_text = _fallback_text_for(ui_actions)

    # If we dropped items the menu does not carry, correct the spoken reply so we
    # never claim to have added something that was silently discarded.
    if unresolved_items:
        names = ", ".join(unresolved_items)
        final_text = (
            f"Sorry, we don't have {names} on the menu. "
            f"{final_text}"
        ).strip()

    return MCPVoiceResponse(
        assistant_text=final_text,
        transcribed_user_text=transcribed_user_text,
        tool_name=None,
        tool_result=tool_result,
        parameters=params,
        ui_actions=ui_actions,
        audio_payload=await _generate_tts_audio(final_text, request.detected_language) if request.is_voice else None,
    )


@router.post("/api/v1/public/mcp/customer-chat/stream")
async def customer_chat_stream(
    request: CustomerMCPRequest,
    db: Session = Depends(get_db),
):
    """Streaming (SSE) variant of the customer chatbot endpoint.

    The buffered endpoint can only speak once the *entire* model reply has
    arrived, so the customer reads the text seconds before hearing it. Here we
    synthesise each sentence as soon as the model emits it, which lets playback
    begin while the rest of the answer is still being generated.

    Event stream:
      {"type": "text",    "chunk": "..."}      incremental model output
      {"type": "audio",   "payload": "<b64>", "text": "..."}  per sentence
      {"type": "final",   ...}                 ui_actions / tool_result / text
      {"type": "error",   "message": "..."}
      [DONE]
    """
    full_prompt, menu_text, menu_names = _build_chat_context(request, db)
    client = GeminiClient(mode="voice" if request.is_voice else "chat")

    async def event_generator():
        def sse(payload: dict) -> str:
            return f"data: {_json.dumps(payload)}\n\n"

        raw_text = ""
        spoken_idx = 0

        try:
            stream = client.generate_json_stream(
                full_prompt,
                audio_base64=request.audio_base64,
                audio_mime_type=request.audio_mime_type,
                response_schema=CUSTOMER_RESPONSE_SCHEMA,
            )

            async for chunk in stream:
                raw_text += chunk
                yield sse({"type": "text", "chunk": chunk})

                if not request.is_voice:
                    continue

                # Pull assistant_text out of the partially-received JSON and
                # speak each completed sentence immediately.
                match = _re.search(
                    r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)', raw_text
                )
                if not match:
                    continue

                text_so_far = match.group(1).replace('\\"', '"').replace("\\n", "\n")
                pending = text_so_far[spoken_idx:]
                boundary = _SENTENCE_BOUNDARY.search(pending)
                if not boundary:
                    continue

                sentence = pending[: boundary.end()].strip()
                if sentence and len(sentence) >= _MIN_TTS_CHARS:
                    spoken_idx += boundary.end()
                    audio = await _generate_tts_audio(sentence, request.detected_language)
                    yield sse({"type": "audio", "payload": audio, "text": sentence})

        except Exception as exc:
            print(f"[CustomerChat/stream] Gemini error: {exc}")
            message = str(exc)
            if "429" in message:
                fallback = "I'm sorry, the AI service is busy right now. Please try again in a moment."
            elif "503" in message:
                fallback = "I'm sorry, the AI service is temporarily unavailable. Please try again later."
            else:
                fallback = "Sorry, I couldn't understand that. Could you please try again?"
            audio = await _generate_tts_audio(fallback) if request.is_voice else None
            yield sse({"type": "final", "assistant_text": fallback, "audio_payload": audio,
                       "ui_actions": [], "tool_result": None})
            yield "data: [DONE]\n\n"
            return

        parsed = client._try_parse_json(raw_text) or {}

        assistant_text = parsed.get("assistant_text") or ""
        if not isinstance(assistant_text, str):
            assistant_text = ""
        assistant_text = assistant_text.strip()

        transcribed_user_text = parsed.get("transcribed_user_text")
        params = parsed.get("params") or {}
        ui_actions = parsed.get("ui_actions") or []
        ui_actions, unresolved_items = resolve_cart_actions(ui_actions, menu_names)

        tool_name = parsed.get("tool_name")
        tool_result = None
        if tool_name and tool_name in CUSTOMER_TOOL_REGISTRY:
            try:
                handler = CUSTOMER_TOOL_REGISTRY[tool_name]["handler"]
                tool_result = handler(db, **params)

                followup_prompt = (
                    f"{build_customer_tool_prompt(is_voice=request.is_voice, is_followup=True, menu_text=menu_text, order_id=request.order_id, current_page=request.current_page, order_type=request.order_type, cart_data=request.cart_data, customer_name=request.customer_name, customer_phone=request.customer_phone)}\n\n"
                    f"The customer asked: {transcribed_user_text or request.prompt}\n"
                    f"You used the tool '{tool_name}' and got this result:\n{tool_result}\n\n"
                    "Give a natural, conversational answer summarizing this data. "
                    "Return JSON with ONLY the key: 'assistant_text'."
                )
                try:
                    second = await client.generate_json(
                        followup_prompt,
                        response_schema=FOLLOWUP_RESPONSE_SCHEMA,
                        max_tokens=256,
                    )
                    if "assistant_text" in second:
                        assistant_text = second["assistant_text"]
                        # The summary is new text, so it has not been spoken yet.
                        spoken_idx = 0
                except Exception as exc2:
                    print(f"[CustomerChat/stream] Follow-up error: {exc2}")
            except Exception as tool_err:
                print(f"[CustomerChat/stream] Tool execution error: {tool_err}")
                assistant_text = assistant_text or (
                    "I couldn't retrieve that information right now. Please try again."
                )

        final_text = assistant_text or _fallback_text_for(ui_actions)

        if unresolved_items:
            names = ", ".join(unresolved_items)
            final_text = f"Sorry, we don't have {names} on the menu. {final_text}".strip()
            # The correction changes the sentence we already spoke, so re-speak.
            spoken_idx = 0

        # Speak whatever has not already gone out as a sentence chunk.
        tail_audio = None
        if request.is_voice:
            remaining = final_text[spoken_idx:].strip() if spoken_idx else final_text
            if remaining:
                tail_audio = await _generate_tts_audio(remaining, request.detected_language)

        yield sse({
            "type": "final",
            "assistant_text": final_text,
            "transcribed_user_text": transcribed_user_text,
            "tool_result": tool_result,
            "parameters": params,
            "ui_actions": ui_actions,
            "audio_payload": tail_audio,
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx from buffering the stream and defeating the point.
            "X-Accel-Buffering": "no",
        },
    )


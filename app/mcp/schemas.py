from pydantic import BaseModel
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Gemini structured-output schema
# --------------------------------------------------------------------------- #
# Supplying this as `responseSchema` forces the model to emit exactly these
# keys with these types. Previously we only set responseMimeType=application/json
# and relied on the prompt + a regex salvage path, so a malformed or truncated
# reply degraded into "Sorry, I couldn't understand that" - a large share of the
# agent's apparent fumbling.
#
# `assistant_text` is listed first in propertyOrdering on purpose: when
# streaming, it means the spoken reply starts arriving before the ui_actions
# array, so TTS can begin almost immediately.
CUSTOMER_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "assistant_text": {
            "type": "STRING",
            "description": "The conversational reply to speak. Must never be empty.",
        },
        "transcribed_user_text": {
            "type": "STRING",
            "description": "Exact transcription of what the customer said.",
        },
        "ui_actions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING"},
                    "item": {"type": "STRING"},
                    "quantity": {"type": "INTEGER"},
                    "page": {"type": "STRING"},
                    "name": {"type": "STRING"},
                    "phone": {"type": "STRING"},
                    "table": {"type": "STRING"},
                    "type": {"type": "STRING"},
                    "stage": {"type": "STRING"},
                    "method": {"type": "STRING"},
                    "region": {"type": "STRING"},
                    "delay_ms": {"type": "INTEGER"},
                },
                "required": ["action"],
            },
        },
    },
    "required": ["assistant_text", "ui_actions"],
    "propertyOrdering": ["assistant_text", "transcribed_user_text", "ui_actions"],
}

# Used for the second "summarise the tool result" pass, which only needs prose.
FOLLOWUP_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"assistant_text": {"type": "STRING"}},
    "required": ["assistant_text"],
}


class MCPToolRequest(BaseModel):
    tool_name: str
    parameters: Dict[str, Any] = {}


class ChatMessage(BaseModel):
    role: str
    text: str


class MCPTextRequest(BaseModel):
    prompt: str
    restaurant_id: Optional[int] = None
    chat_history: Optional[List[ChatMessage]] = None
    audio_base64: Optional[str] = None
    # Actual MediaRecorder container. Chrome/Firefox -> audio/webm,
    # Safari -> audio/mp4. Mislabelling this makes Gemini mis-transcribe.
    audio_mime_type: Optional[str] = None
    is_voice: bool = False


class MCPVoiceRequest(BaseModel):
    transcribed_text: str
    restaurant_id: Optional[int] = None
    chat_history: Optional[List[ChatMessage]] = None
    audio_base64: Optional[str] = None
    audio_mime_type: Optional[str] = None
    is_voice: bool = True


class MCPTtsRequest(BaseModel):
    text: str
    audio_encoding: Optional[str] = "MP3"


class MCPResponse(BaseModel):
    assistant_text: str
    transcribed_user_text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_result: Optional[Any] = None
    action: Optional[str] = None
    actions: Optional[List[Dict[str, Any]]] = None
    ui_actions: Optional[List[Dict[str, Any]]] = None
    parameters: Optional[Dict[str, Any]] = None


class MCPVoiceResponse(MCPResponse):
    audio_payload: Optional[str] = None


class CustomerMCPRequest(BaseModel):
    """Public (no-auth) request schema for the customer chatbot."""
    prompt: str = ""
    chat_history: Optional[List[ChatMessage]] = None
    audio_base64: Optional[str] = None  # audio/webm base64 from MediaRecorder
    audio_mime_type: Optional[str] = None  # actual container (webm/mp4/ogg/wav)
    is_voice: bool = False
    restaurant_id: Optional[int] = None
    order_id: Optional[str] = None
    current_page: Optional[str] = None
    order_type: Optional[str] = None
    cart_data: Optional[List[Dict[str, Any]]] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    # Journey-stage fields
    flow_stage: Optional[str] = None          # e.g. GREETING, COLLECT_NAME, MENU_BROWSE …
    table_number: Optional[str] = None        # dine-in table
    payment_status: Optional[str] = None      # pending | processing | paid
    order_status: Optional[str] = None        # PENDING | PREPARING | READY | SERVED
    detected_language: Optional[str] = None   # hint from frontend
    session_id: Optional[str] = None          # unique session / tab identifier


from pydantic import BaseModel
from typing import Any, Dict, List, Optional


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
    is_voice: bool = False


class MCPVoiceRequest(BaseModel):
    transcribed_text: str
    restaurant_id: Optional[int] = None
    chat_history: Optional[List[ChatMessage]] = None
    audio_base64: Optional[str] = None
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


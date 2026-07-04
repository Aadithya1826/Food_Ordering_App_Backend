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


class MCPVoiceResponse(MCPResponse):
    audio_payload: Optional[str] = None

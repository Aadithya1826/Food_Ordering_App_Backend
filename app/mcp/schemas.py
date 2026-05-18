from pydantic import BaseModel
from typing import Any, Dict, List, Optional


class MCPToolRequest(BaseModel):
    tool_name: str
    parameters: Dict[str, Any] = {}


class MCPTextRequest(BaseModel):
    prompt: str
    restaurant_id: Optional[int] = None


class MCPVoiceRequest(BaseModel):
    transcribed_text: str
    restaurant_id: Optional[int] = None


class MCPTtsRequest(BaseModel):
    text: str
    audio_encoding: Optional[str] = "MP3"


class MCPResponse(BaseModel):
    assistant_text: str
    tool_name: Optional[str] = None
    tool_result: Optional[Any] = None


class MCPVoiceResponse(MCPResponse):
    audio_payload: Optional[Dict[str, Any]] = None

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class ChatMessage(BaseModel):
    role: str
    text: str


class MobileCustomerMCPRequest(BaseModel):
    """Schema for the MOBILE customer voice agent request."""
    message: str = ""
    audio_base64: Optional[str] = None
    chat_history: Optional[List[ChatMessage]] = None
    
    # Customer context
    customer_id: Optional[int] = None
    phone: Optional[str] = None
    restaurant_id: Optional[int] = None
    
    # Global state context
    screen: Optional[Dict[str, Any]] = None 
    app_context: Optional[Dict[str, Any]] = None 
    cart: Optional[List[Dict[str, Any]]] = None


class UIAction(BaseModel):
    action: str
    route: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    menu_item_id: Optional[int] = None
    quantity: Optional[int] = None
    order_type: Optional[str] = None
    order_id: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    # Flexible catch-all for other fields
    payload: Optional[Dict[str, Any]] = None


class MobileAgentData(BaseModel):
    categories: Optional[List[Dict[str, Any]]] = None
    menu_items: Optional[List[Dict[str, Any]]] = None
    orders: Optional[List[Dict[str, Any]]] = None
    tracking: Optional[Dict[str, Any]] = None


class MobileCustomerMCPResponse(BaseModel):
    """Schema for the MOBILE customer voice agent response."""
    assistant_text: str
    ui_actions: List[UIAction] = Field(default_factory=list)
    data: MobileAgentData = Field(default_factory=MobileAgentData)
    requires_confirmation: bool = False
    audio_base64: Optional[str] = None
    transcribed_user_text: Optional[str] = None

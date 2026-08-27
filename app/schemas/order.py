from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class OrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int
    price: float

class OrderItemResponse(OrderItemCreate):
    id: int
    order_id: int

    class Config:
        from_attributes = True

class DeliveryAddressCreate(BaseModel):
    name: str
    phone: str
    address_line: str
    city: str
    pincode: str

class DeliveryAddressResponse(DeliveryAddressCreate):
    id: int
    restaurant_id: Optional[int] = None
    
    class Config:
        from_attributes = True

class OrderCreate(BaseModel):
    table_id: Optional[int] = None
    order_type: str = "DINE_IN"
    delivery_address_id: Optional[int] = None
    items: List[OrderItemCreate]

class OrderStatusUpdate(BaseModel):
    status: str

class OrderPaymentStatusUpdate(BaseModel):
    payment_status: str

class OrderResponse(BaseModel):
    id: int
    table_id: Optional[int] = None
    order_type: str = "DINE_IN"
    delivery_address_id: Optional[int] = None
    status: str
    total_amount: float
    created_at: datetime
    updated_at: datetime
    items: List[OrderItemResponse]

    class Config:
        from_attributes = True

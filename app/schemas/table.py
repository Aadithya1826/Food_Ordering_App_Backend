from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TableCreate(BaseModel):
    table_number: str
    qr_code: Optional[str] = None
    capacity: Optional[int] = 4
    is_active: Optional[bool] = True

class TableResponse(BaseModel):
    id: int
    restaurant_id: Optional[int]
    table_number: str
    qr_code: Optional[str]
    capacity: int
    is_active: bool
    current_order_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True

class TableUpdate(BaseModel):
    table_number: Optional[str] = None
    qr_code: Optional[str] = None
    capacity: Optional[int] = None
    is_active: Optional[bool] = None

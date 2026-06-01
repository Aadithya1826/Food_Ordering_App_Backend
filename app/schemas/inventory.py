from pydantic import BaseModel
from typing import Optional

class InventoryUpdate(BaseModel):
    name: Optional[str] = None
    open_stock: Optional[float] = None
    purchase: Optional[float] = None
    total: Optional[float] = None
    issue: Optional[float] = None
    balance: Optional[float] = None
    unit: Optional[str] = None
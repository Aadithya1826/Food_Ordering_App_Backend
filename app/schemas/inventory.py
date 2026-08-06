from pydantic import BaseModel
from typing import Optional
from datetime import date

class InventoryUpdate(BaseModel):
    name: Optional[str] = None
    open_stock: Optional[float] = None
    purchase: Optional[float] = None
    total: Optional[float] = None
    issue: Optional[float] = None
    balance: Optional[float] = None
    unit: Optional[str] = None
    report_date: Optional[date] = None
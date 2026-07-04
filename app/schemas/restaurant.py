from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class RestaurantCreateRequest(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None


class RestaurantUpdateRequest(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    gst_number: Optional[str] = None
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    order_notifications: Optional[int] = None
    low_stock_alerts: Optional[int] = None
    daily_email_reports: Optional[int] = None
    auto_print_bills: Optional[int] = None
    print_kot: Optional[int] = None
    tax_rate: Optional[float] = None
    service_charge: Optional[float] = None
    packaging_charge: Optional[float] = None

class RestaurantResponse(BaseModel):
    id: int
    name: str
    address: Optional[str]
    phone: Optional[str]
    gst_number: Optional[str] = None
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    order_notifications: Optional[int] = None
    low_stock_alerts: Optional[int] = None
    daily_email_reports: Optional[int] = None
    auto_print_bills: Optional[int] = None
    print_kot: Optional[int] = None
    tax_rate: Optional[float] = None
    service_charge: Optional[float] = None
    packaging_charge: Optional[float] = None
    created_at: datetime
    manager_name: Optional[str] = None
    orders: Optional[int] = 0
    revenue: Optional[float] = 0.0
    venues: Optional[int] = 1

    class Config:
        from_attributes = True

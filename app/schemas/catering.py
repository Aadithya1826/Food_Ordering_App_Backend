from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Union
import datetime
import uuid

class CateringPackageResponse(BaseModel):
    name: str
    code: str
    price: float
    minimum_order_quantity: int
    is_available: bool
    items_summary: Optional[str] = None

    class Config:
        from_attributes = True

class CateringPackageItemResponse(BaseModel):
    id: int
    name: str
    group: str = Field(..., alias="customization_group")
    is_swappable: bool
    is_removable: bool

    class Config:
        from_attributes = True
        populate_by_name = True

class CateringCategoryResponse(BaseModel):
    name: str
    items: List[CateringPackageItemResponse]

class CateringPackageWithItemsResponse(BaseModel):
    package: CateringPackageResponse
    categories: List[CateringCategoryResponse]

class CateringSwapOptionResponse(BaseModel):
    id: int
    name: str
    group: str
    price_change_per_person: float

    class Config:
        from_attributes = True

class CateringSwapOptionsListResponse(BaseModel):
    original_item: dict
    options: List[CateringSwapOptionResponse]

class CateringSessionCreate(BaseModel):
    customer_id: int
    restaurant_id: Optional[int] = None
    package_code: str
    guest_count: int

class CateringSessionEventUpdate(BaseModel):
    event_name: Optional[str] = None
    event_date: Optional[str] = None # format YYYY-MM-DD
    serving_time: Optional[str] = None
    occasion: Optional[str] = None
    service_type: Optional[str] = None
    delivery_address_id: Optional[int] = None
    spice_level: Optional[str] = None
    dietary_notes: Optional[str] = None

class CateringCustomizationCreate(BaseModel):
    action_type: Literal["ADD", "REMOVE", "REPLACE"]
    original_item_id: Optional[int] = None
    new_item_id: Optional[int] = None

class CateringCustomizationResponse(BaseModel):
    id: int
    action_type: str
    original_item_id: Optional[int] = None
    original_item_name: Optional[str] = None
    new_item_id: Optional[int] = None
    new_item_name: Optional[str] = None
    price_adjustment_per_person: float
    total_price_adjustment: float

    class Config:
        from_attributes = True

class CateringAddonCreate(BaseModel):
    addon_type: str
    addon_name: str
    quantity: int
    unit_price: float
    pricing_type: Literal["PER_PERSON", "PER_UNIT", "FLAT"]

class CateringAddonResponse(BaseModel):
    id: int
    addon_type: str
    addon_name: str
    quantity: int
    unit_price: float
    pricing_type: str
    total_price: float

    class Config:
        from_attributes = True

import uuid
from typing import Union

class CateringQuoteResponse(BaseModel):
    session_id: Union[str, uuid.UUID] = Field(..., alias="id")
    status: str
    package_name: str
    package_price: float
    guest_count: int
    base_amount: float
    customization_amount: float
    addon_amount: float
    transport_charge: float
    service_charge: float
    cgst_amount: float
    sgst_amount: float
    total_amount: float
    advance_percentage: float
    advance_amount: float
    balance_amount: float

    class Config:
        from_attributes = True
        populate_by_name = True

class CateringSessionResponse(CateringQuoteResponse):
    event_name: Optional[str]
    event_date: Optional[datetime.datetime]
    serving_time: Optional[Union[str, datetime.time]] = None
    occasion: Optional[str]
    service_type: Optional[str]
    delivery_address_id: Optional[int]
    spice_level: Optional[str]
    dietary_notes: Optional[str]
    customizations: List[CateringCustomizationResponse] = []
    addons: List[CateringAddonResponse] = []

    class Config:
        from_attributes = True
        populate_by_name = True

class CateringPaymentRequest(BaseModel):
    payment_amount: float

class CateringPaymentVerifyRequest(BaseModel):
    amount: float
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    payment_method: Optional[str] = None

class CateringBalancePaymentRequest(BaseModel):
    payment_amount: float

class CateringBalancePaymentVerifyRequest(BaseModel):
    amount: float
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    payment_method: Optional[str] = None

class CateringPaymentResponse(BaseModel):
    id: int
    transaction_id: Optional[str]
    amount: float
    payment_type: str
    payment_method: str
    payment_status: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class CateringOrderResponse(BaseModel):
    id: int
    catering_session_id: Optional[Union[str, uuid.UUID]] = None
    package_name: str
    event_name: Optional[str] = None
    event_date: Optional[datetime.datetime] = None
    guest_count: int
    total_amount: float
    advance_amount: float
    paid_amount: float = 0.0
    balance_amount: float = 0.0
    full_payment_due_date: Optional[datetime.date] = None
    payment_status: Optional[str] = None
    order_status: str
    payments: List[CateringPaymentResponse] = []

    class Config:
        from_attributes = True

class CateringOrderListResponse(BaseModel):
    data: List[CateringOrderResponse]

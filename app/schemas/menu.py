from pydantic import BaseModel
from typing import Optional

class MenuCategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    restaurant_id: Optional[int] = None

class MenuCategoryResponse(MenuCategoryCreate):
    id: int

    class Config:
        from_attributes = True

class MenuItemCreate(BaseModel):
    item_code: Optional[str] = None
    ac_type: Optional[str] = "Both"
    name: str
    description: Optional[str] = None
    price: float
    quantity: int = 0
    category_id: int
    restaurant_id: Optional[int] = None
    is_available: bool = True
    image_url: Optional[str] = None

class MenuItemResponse(MenuItemCreate):
    id: int

    class Config:
        from_attributes = True

class MenuItemUpdate(BaseModel):
    item_code: Optional[str] = None
    ac_type: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    is_available: Optional[bool] = None
    image_url: Optional[str] = None

class CateringOrderMenuBase(BaseModel):
    name: str
    code: Optional[str] = None
    price: float
    description: Optional[str] = None
    minimum_order_quantity: int = 50
    restaurant_id: Optional[int] = None
    is_available: bool = True
    category_name: Optional[str] = None
    item_name: Optional[str] = None
    customization_group: Optional[str] = None
    is_swappable: Optional[bool] = False
    is_removable: Optional[bool] = False
    add_price: Optional[float] = 0.0
    remove_price: Optional[float] = 0.0
    same_group_replace_price: Optional[float] = 0.0
    upgrade_group: Optional[str] = None
    upgrade_price: Optional[float] = 0.0
    display_order: Optional[int] = 0

class CateringOrderMenuResponse(CateringOrderMenuBase):
    id: int

    class Config:
        from_attributes = True

class CateringOrderMenuCreate(CateringOrderMenuBase):
    pass

class CateringOrderMenuUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    minimum_order_quantity: Optional[int] = None
    is_available: Optional[bool] = None
    category_name: Optional[str] = None
    item_name: Optional[str] = None
    customization_group: Optional[str] = None
    is_swappable: Optional[bool] = None
    is_removable: Optional[bool] = None
    add_price: Optional[float] = None
    remove_price: Optional[float] = None
    same_group_replace_price: Optional[float] = None
    upgrade_group: Optional[str] = None
    upgrade_price: Optional[float] = None
    display_order: Optional[int] = None


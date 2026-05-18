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
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    is_available: Optional[bool] = None
    image_url: Optional[str] = None

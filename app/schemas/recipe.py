from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class RecipeIngredientBase(BaseModel):
    inventory_item_name: str
    quantity: float
    unit: str

class RecipeIngredientCreate(RecipeIngredientBase):
    pass

class RecipeIngredientResponse(RecipeIngredientBase):
    id: int
    menu_item_id: int
    restaurant_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True

class RecipeUpdatePayload(BaseModel):
    menu_item_id: int
    ingredients: List[RecipeIngredientCreate]

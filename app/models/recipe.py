from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from ..db import Base
from datetime import datetime

class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, index=True, nullable=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), index=True)
    inventory_item_name = Column(String, index=True)
    quantity = Column(Float)
    unit = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    menu_item = relationship("MenuItem")

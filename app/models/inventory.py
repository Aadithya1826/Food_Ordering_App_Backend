from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from ..db import Base
from datetime import datetime

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"))
    name = Column(String)
    open_stock = Column(Float, default=0.0)
    purchase = Column(Float, default=0.0)
    total = Column(Float, default=0.0)
    issue = Column(Float, default=0.0)
    balance = Column(Float, default=0.0)
    unit = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
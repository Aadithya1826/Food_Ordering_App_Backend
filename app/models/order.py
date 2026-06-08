from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from ..db import Base
from datetime import datetime
import enum

class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY = "READY"
    SERVED = "SERVED"
    CANCELLED = "CANCELLED"

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, index=True, nullable=True)
    table_id = Column(Integer, ForeignKey("tables.id"), index=True)
    status = Column(String, index=True, nullable=True)
    payment_method = Column(String, nullable=True)
    payment_status = Column(String, nullable=True)
    total_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    table = relationship("Table", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), index=True)
    quantity = Column(Integer)
    price = Column(Float)
    
    order = relationship("Order", back_populates="items")
    menu_item = relationship("MenuItem")

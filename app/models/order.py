from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
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

class DeliveryAddress(Base):
    __tablename__ = "delivery_addresses"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, index=True)
    name = Column(String)
    phone = Column(String)
    address_line = Column(String)
    city = Column(String)
    pincode = Column(String)
    
    orders = relationship("Order", back_populates="delivery_address")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, index=True, nullable=True)
    table_id = Column(String, ForeignKey("tables.id"), index=True, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    order_type = Column(String, default="DINE_IN", index=True) # DINE_IN, TAKEAWAY, DELIVERY
    delivery_address_id = Column(Integer, ForeignKey("delivery_addresses.id"), nullable=True)
    status = Column(String, index=True, nullable=True)
    payment_method = Column(String, nullable=True)
    payment_status = Column(String, nullable=True)
    total_amount = Column(Float, nullable=True)
    
    # Delivery and Advanced Order Fields
    delivery_address_id = Column(Integer, ForeignKey("customer_addresses.id"), nullable=True)
    delivery_address_snapshot = Column(JSONB, nullable=True)
    delivery_instructions = Column(String, nullable=True)
    delivery_status = Column(String, nullable=True)
    delivery_fee = Column(Float, nullable=True)
    packaging_fee = Column(Float, nullable=True)
    gst_amount = Column(Float, nullable=True)
    tip_amount = Column(Float, nullable=True)
    order_type = Column(String, nullable=True, default="DINE_IN")
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    table = relationship("Table", back_populates="orders")
    delivery_address = relationship("DeliveryAddress", back_populates="orders")
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

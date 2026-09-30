import uuid
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Text, ForeignKey
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship
from ..db import Base
from datetime import datetime

class CateringSession(Base):
    __tablename__ = "catering_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True, nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=True)
    package_name = Column(String, nullable=False)
    package_code = Column(String, nullable=False)
    package_price = Column(Float, nullable=False)
    event_name = Column(String, nullable=True)
    event_date = Column(DateTime, nullable=True)
    serving_time = Column(String, nullable=True)
    occasion = Column(String, nullable=True)
    guest_count = Column(Integer, nullable=False)
    service_type = Column(String, nullable=True)
    delivery_address_id = Column(Integer, ForeignKey("customer_addresses.id"), nullable=True)
    delivery_address_snapshot = Column(String, nullable=True) # or JSON
    spice_level = Column(String, nullable=True)
    dietary_notes = Column(Text, nullable=True)
    base_amount = Column(Float, default=0.0)
    customization_amount = Column(Float, default=0.0)
    addon_amount = Column(Float, default=0.0)
    transport_charge = Column(Float, default=0.0)
    service_charge = Column(Float, default=0.0)
    cgst_amount = Column(Float, default=0.0)
    sgst_amount = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)
    advance_percentage = Column(Float, default=50.0)
    advance_amount = Column(Float, default=0.0)
    balance_amount = Column(Float, default=0.0)
    status = Column(String, default="DRAFT", index=True) # DRAFT, QUOTED, PAYMENT_PENDING, CONVERTED
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customizations = relationship("CateringSessionCustomization", back_populates="session", cascade="all, delete-orphan")
    addons = relationship("CateringSessionAddon", back_populates="session", cascade="all, delete-orphan")

class CateringSessionCustomization(Base):
    __tablename__ = "catering_session_customizations"

    id = Column(Integer, primary_key=True, index=True)
    catering_session_id = Column(String(36), ForeignKey("catering_sessions.id"), index=True, nullable=False)
    action_type = Column(String, nullable=False) # ADD, REMOVE, REPLACE
    original_item_id = Column(Integer, nullable=True)
    original_item_name = Column(String, nullable=True)
    original_category = Column(String, nullable=True)
    original_group = Column(String, nullable=True)
    new_item_id = Column(Integer, nullable=True)
    new_item_name = Column(String, nullable=True)
    new_category = Column(String, nullable=True)
    new_group = Column(String, nullable=True)
    price_adjustment_per_person = Column(Float, default=0.0)
    guest_count = Column(Integer, default=0)
    total_price_adjustment = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("CateringSession", back_populates="customizations")

class CateringSessionAddon(Base):
    __tablename__ = "catering_session_addons"

    id = Column(Integer, primary_key=True, index=True)
    catering_session_id = Column(String(36), ForeignKey("catering_sessions.id"), index=True, nullable=False)
    addon_type = Column(String, nullable=False)
    addon_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    pricing_type = Column(String, nullable=False) # PER_PERSON, PER_UNIT, FLAT
    total_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("CateringSession", back_populates="addons")

class CateringOrder(Base):
    __tablename__ = "catering_orders"

    id = Column(Integer, primary_key=True, index=True)
    catering_session_id = Column(String(36), unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True, nullable=False)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=True)
    package_name = Column(String, nullable=False)
    package_code = Column(String, nullable=False)
    package_price = Column(Float, nullable=False)
    event_name = Column(String, nullable=True)
    event_date = Column(DateTime, nullable=True)
    serving_time = Column(String, nullable=True)
    occasion = Column(String, nullable=True)
    guest_count = Column(Integer, nullable=False)
    service_type = Column(String, nullable=True)
    delivery_address_id = Column(Integer, ForeignKey("customer_addresses.id"), nullable=True)
    delivery_address_snapshot = Column(String, nullable=True)
    spice_level = Column(String, nullable=True)
    dietary_notes = Column(Text, nullable=True)
    base_amount = Column(Float, default=0.0)
    customization_amount = Column(Float, default=0.0)
    addon_amount = Column(Float, default=0.0)
    transport_charge = Column(Float, default=0.0)
    service_charge = Column(Float, default=0.0)
    cgst_amount = Column(Float, default=0.0)
    sgst_amount = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)
    advance_percentage = Column(Float, default=50.0)
    advance_amount = Column(Float, default=0.0)
    order_status = Column(String, default="CONFIRMED")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customizations = relationship("CateringOrderCustomization", back_populates="order", cascade="all, delete-orphan")
    addons = relationship("CateringOrderAddon", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("CateringPayment", back_populates="order", cascade="all, delete-orphan")

    paid_amount = Column(Float, default=0.0)
    balance_amount = Column(Float, default=0.0)
    payment_status = Column(String, default="PENDING")
    full_payment_due_date = Column(DateTime, nullable=True)

class CateringOrderCustomization(Base):
    __tablename__ = "catering_order_customizations"

    id = Column(Integer, primary_key=True, index=True)
    catering_order_id = Column(Integer, ForeignKey("catering_orders.id"), index=True, nullable=False)
    action_type = Column(String)
    original_item_name = Column(String, nullable=True)
    original_category = Column(String, nullable=True)
    original_group = Column(String, nullable=True)
    
    new_item_name = Column(String, nullable=True)
    new_category = Column(String, nullable=True)
    new_group = Column(String, nullable=True)
    
    price_adjustment_per_person = Column(Float, default=0.0)
    guest_count = Column(Integer)
    total_price_adjustment = Column(Float, default=0.0)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("CateringOrder", back_populates="customizations")

class CateringOrderAddon(Base):
    __tablename__ = "catering_order_addons"

    id = Column(Integer, primary_key=True, index=True)
    catering_order_id = Column(Integer, ForeignKey("catering_orders.id"), index=True, nullable=False)
    addon_type = Column(String, nullable=False)
    addon_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    pricing_type = Column(String, nullable=False)
    total_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("CateringOrder", back_populates="addons")

class CateringPayment(Base):
    __tablename__ = "catering_payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True, nullable=False)
    catering_order_id = Column(Integer, ForeignKey("catering_orders.id"), index=True, nullable=False)
    transaction_id = Column(String, unique=True, index=True, nullable=False)
    amount = Column(Float, nullable=False)
    payment_type = Column(String, default="ADVANCE", nullable=False)
    payment_method = Column(String, nullable=True)
    payment_status = Column(String, default="SUCCESS")
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("CateringOrder", back_populates="payments")

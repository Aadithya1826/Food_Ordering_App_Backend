from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, UniqueConstraint
from ..db import Base
from datetime import datetime

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String)
    email = Column(String, nullable=True)
    address = Column(String, nullable=True)
    loyalty_points = Column(Integer, default=0)
    profile_picture_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CustomerAddress(Base):
    __tablename__ = "customer_addresses"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True)
    address_type = Column(String)
    flat_house_no = Column(String)
    floor = Column(String, nullable=True)
    building_apartment_name = Column(String, nullable=True)
    landmark = Column(String, nullable=True)
    full_address = Column(String)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    city = Column(String)
    state = Column(String)
    pincode = Column(String)
    contact_name = Column(String)
    contact_phone = Column(String)
    delivery_instructions = Column(String, nullable=True)
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LoyaltyTransaction(Base):
    """
    Idempotent loyalty point ledger.
    A UNIQUE constraint on (order_id, transaction_type) prevents double-crediting
    the same order. Backend services call this internally — never the customer frontend.

    transaction_type values: ORDER_CREDIT | REDEMPTION | ADJUSTMENT | REVERSAL
    """
    __tablename__ = "loyalty_transactions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    points = Column(Integer, nullable=False)
    transaction_type = Column(String(50), nullable=False)  # ORDER_CREDIT, REDEMPTION, ADJUSTMENT, REVERSAL
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("order_id", "transaction_type", name="uq_loyalty_order_type"),
    )


class CustomerFavorite(Base):
    """
    Per-customer manual favorites (heart-tapped menu items).
    These are distinct from AI/history-based recommendations.
    """
    __tablename__ = "customer_favorites"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("customer_id", "menu_item_id", name="uq_customer_favorite"),
    )

from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey
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

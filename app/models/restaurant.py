from sqlalchemy import Column, Integer, String, Text, DateTime, Float
from ..db import Base
from datetime import datetime


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    
    # New Settings Fields
    gst_number = Column(String, nullable=True)
    opening_time = Column(String, nullable=True)
    closing_time = Column(String, nullable=True)
    
    order_notifications = Column(Integer, default=1)  # 1 for True, 0 for False
    low_stock_alerts = Column(Integer, default=1)
    daily_email_reports = Column(Integer, default=1)
    
    auto_print_bills = Column(Integer, default=1)
    print_kot = Column(Integer, default=0)
    
    tax_rate = Column(Float, default=5.0)
    service_charge = Column(Float, default=0.0)
    packaging_charge = Column(Float, default=10.0)

    created_at = Column(DateTime, default=datetime.utcnow)

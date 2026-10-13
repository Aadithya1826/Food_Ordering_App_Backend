from sqlalchemy import Column, Integer, BigInteger, String, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from ..db import Base
from datetime import datetime

class DineInSession(Base):
    __tablename__ = "dine_in_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_uid = Column(String(64), unique=True, nullable=False, index=True)
    restaurant_id = Column(BigInteger, ForeignKey("restaurants.id"), nullable=False, index=True)
    table_id = Column(BigInteger, ForeignKey("tables.id"), nullable=False, index=True)
    customer_phone = Column(String(32), nullable=True)
    status = Column(String(32), default="ACTIVE", nullable=False, index=True)
    payment_status = Column(String(32), default="Pending", nullable=False)
    total_amount = Column(Numeric(10, 2), default=0.00)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    table = relationship("Table", backref="dine_in_sessions")
    orders = relationship("Order", back_populates="dine_in_session", order_by="Order.id.asc()")

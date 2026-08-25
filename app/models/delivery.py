from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, BigInteger
from sqlalchemy.orm import relationship
from ..db import Base
from datetime import datetime

class DeliveryPartner(Base):
    __tablename__ = "delivery_partners"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(150))
    phone = Column(String(20), unique=True)
    email = Column(String(150), nullable=True)
    profile_image = Column(Text, nullable=True)
    vehicle_type = Column(String(30))
    vehicle_number = Column(String(50))
    rating = Column(Float, default=0.0)
    total_ratings = Column(Integer, default=0)
    is_online = Column(Boolean, default=False)
    is_available = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    current_latitude = Column(Float, nullable=True)
    current_longitude = Column(Float, nullable=True)
    last_location_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DeliveryAssignment(Base):
    __tablename__ = "delivery_assignments"

    id = Column(BigInteger, primary_key=True, index=True)
    order_id = Column(BigInteger, ForeignKey("orders.id"), index=True)
    rider_id = Column(BigInteger, ForeignKey("delivery_partners.id"), index=True)
    status = Column(String(40), index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    arrived_restaurant_at = Column(DateTime, nullable=True)
    picked_up_at = Column(DateTime, nullable=True)
    arrived_customer_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    rider = relationship("DeliveryPartner")
    order = relationship("Order", foreign_keys=[order_id])

class RiderLocation(Base):
    __tablename__ = "rider_locations"

    id = Column(BigInteger, primary_key=True, index=True)
    rider_id = Column(BigInteger, ForeignKey("delivery_partners.id"), index=True)
    order_id = Column(BigInteger, ForeignKey("orders.id"), index=True, nullable=True)
    latitude = Column(Float)
    longitude = Column(Float)
    accuracy = Column(Float, nullable=True)
    speed = Column(Float, nullable=True)
    heading = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)

class DeliveryStatusHistory(Base):
    __tablename__ = "delivery_status_history"

    id = Column(BigInteger, primary_key=True, index=True)
    order_id = Column(BigInteger, ForeignKey("orders.id"), index=True)
    delivery_assignment_id = Column(BigInteger, ForeignKey("delivery_assignments.id"), index=True, nullable=True)
    rider_id = Column(BigInteger, ForeignKey("delivery_partners.id"), index=True, nullable=True)
    status = Column(String(40))
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

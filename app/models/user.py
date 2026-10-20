from sqlalchemy import Column, Integer, String, Boolean, DateTime
from ..db import Base
import enum
from datetime import datetime

class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    WAITER = "WAITER"
    CASHIER = "CASHIER"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, index=True, nullable=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    # Optional phone for waiters — enforced unique via partial index in DB
    # (NULL values do not conflict). Non-waiter users keep phone=None.
    phone = Column(String(20), nullable=True)
    password_hash = Column(String)
    role = Column(String, index=True, nullable=True)
    is_active = Column(Boolean, default=True)
    # is_approved: waiters start False (pending admin approval + hotel assignment)
    # All existing non-waiter users are implicitly approved (NULL treated as True)
    is_approved = Column(Boolean, nullable=True, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)

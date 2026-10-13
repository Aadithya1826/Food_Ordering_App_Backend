import os
import json
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import sessionmaker
# pyrefly: ignore [missing-import]
from sqlalchemy import create_engine
from app.models.order import Order, OrderItem
from app.models.delivery import DeliveryAssignment
from app.models.restaurant import Restaurant

DATABASE_URL = "postgresql://food_admin:foodadmin%40123@banking-db.cnkegcm24ikf.ap-south-2.rds.amazonaws.com:5432/food_ordering_db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

assignment = db.query(DeliveryAssignment).order_by(DeliveryAssignment.id.desc()).first()
print("Assignment:", assignment.id, "Order:", assignment.order_id)
order = db.query(Order).filter(Order.id == assignment.order_id).first()
print("Order:", order.id if order else "None")

order_items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
print("Order Items length:", len(order_items))

items = []
for oi in order_items:
    items.append({
        "id": oi.id,
        "quantity": oi.quantity
    })
print("Items:", items)

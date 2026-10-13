import os
import json
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from app.models.order import Order, OrderItem
from app.models.delivery import DeliveryAssignment
from app.models.restaurant import Restaurant
from app.routes.rider import format_assignment_data

DATABASE_URL = "postgresql://food_admin:foodadmin%40123@banking-db.cnkegcm24ikf.ap-south-2.rds.amazonaws.com:5432/food_ordering_db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

assignment = db.query(DeliveryAssignment).filter(DeliveryAssignment.status == "UNASSIGNED").order_by(DeliveryAssignment.id.desc()).first()
if assignment:
    order = db.query(Order).filter(Order.id == assignment.order_id).first()
    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first() if order else None
    data = format_assignment_data(assignment, order, restaurant, db)
    print(json.dumps(data, indent=2))
else:
    print("No UNASSIGNED assignment found")

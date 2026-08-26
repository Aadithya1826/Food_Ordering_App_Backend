from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List
from ..models.delivery import DeliveryPartner, DeliveryAssignment
from ..models.order import Order, OrderItem
from ..models.restaurant import Restaurant
from ..utils.auth import create_token
from ..utils.dependencies import get_current_rider

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class RiderLoginPayload(BaseModel):
    phone: str
    otp: Optional[str] = None
    password: Optional[str] = None

@router.post("/api/v1/rider/auth/login")
def rider_login(payload: RiderLoginPayload, db: Session = Depends(get_db)):
    if payload.otp != "1234" and payload.password != "1234":
        raise HTTPException(status_code=400, detail="Invalid OTP/Password")
    
    phone = payload.phone.strip()
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.phone == phone).first()
    
    if not rider:
        raise HTTPException(status_code=401, detail="Rider account not found")
        
    class MockUser:
        id = rider.id
        role = "RIDER"
    
    token = create_token(MockUser(), account_type="RIDER")
    return {
        "token": token,
        "rider": {
            "id": rider.id,
            "name": rider.name,
            "phone": rider.phone,
            "is_online": rider.is_online
        }
    }

@router.get("/api/v1/rider/delivery-requests")
def get_delivery_requests(current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status == "ASSIGNED"
    ).all()
    
    requests = []
    for assignment in assignments:
        order = db.query(Order).filter(Order.id == assignment.order_id).first()
        if order:
            restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
            items_count = db.query(OrderItem).filter(OrderItem.order_id == order.id).count()
            
            requests.append({
                "assignment_id": assignment.id,
                "order_id": order.id,
                "status": assignment.status,
                "assigned_at": assignment.assigned_at,
                "restaurant": {
                    "id": restaurant.id if restaurant else None,
                    "name": restaurant.name if restaurant else None,
                    "address": restaurant.address if restaurant else None,
                    "latitude": restaurant.latitude if restaurant else None,
                    "longitude": restaurant.longitude if restaurant else None
                },
                "delivery_address": order.delivery_address_snapshot,
                "items_count": items_count,
                "total_amount": order.total_amount,
                "payment_method": order.payment_method if hasattr(order, "payment_method") and order.payment_method else "UPI"
            })
    return {"delivery_requests": requests}

@router.get("/api/v1/rider/deliveries/current")
def get_current_delivery(current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status.notin_(["DELIVERED", "REJECTED", "CANCELLED", "FAILED"])
    ).first()
    
    if not assignment:
        return None
        
    return {
        "assignment_id": assignment.id,
        "order_id": assignment.order_id,
        "status": assignment.status,
        "assigned_at": assignment.assigned_at
    }

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from ..db import SessionLocal
from ..models.delivery import DeliveryPartner, DeliveryAssignment, DeliveryStatusHistory
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
    # Find assignments specifically assigned to this rider or available open orders
    assignments = db.query(DeliveryAssignment).filter(
        (DeliveryAssignment.rider_id == rider_id) & (DeliveryAssignment.status.in_(["ASSIGNED", "PENDING"])),
    ).all()

    # Also check unassigned assignments
    unassigned = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.status == "UNASSIGNED"
    ).all()
    
    all_assignments = list(assignments) + list(unassigned)
    
    requests = []
    seen_orders = set()
    for assignment in all_assignments:
        if assignment.order_id in seen_orders:
            continue
        seen_orders.add(assignment.order_id)
        
        order = db.query(Order).filter(Order.id == assignment.order_id).first()
        if order and order.status not in ["CANCELLED", "COMPLETED", "SERVED"]:
            restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
            items_list = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
            
            snap = order.delivery_address_snapshot or {}
            cust_lat = snap.get("latitude")
            cust_lng = snap.get("longitude")
            cust_addr = snap.get("full_address", "Delivery Address")
            cust_name = snap.get("contact_name", "Customer")
            cust_phone = snap.get("contact_phone", order.customer_phone)
            instructions = snap.get("delivery_instructions", order.delivery_instructions)

            items_data = [
                {
                    "name": i.menu_item.name if getattr(i, "menu_item", None) else "Item",
                    "quantity": i.quantity,
                    "price": i.price
                }
                for i in items_list
            ]

            requests.append({
                "assignment_id": assignment.id,
                "order_id": order.id,
                "display_id": f"ORD-{str(order.id).zfill(6)}",
                "status": assignment.status,
                "assigned_at": assignment.assigned_at,
                "restaurant": {
                    "id": restaurant.id if restaurant else 1,
                    "name": restaurant.name if restaurant else "Restaurant",
                    "address": restaurant.address if restaurant else "",
                    "latitude": restaurant.latitude if restaurant and restaurant.latitude else 13.0418,
                    "longitude": restaurant.longitude if restaurant and restaurant.longitude else 80.2341
                },
                "delivery_address": snap,
                "customer": {
                    "name": cust_name,
                    "phone": cust_phone,
                    "address": cust_addr,
                    "latitude": cust_lat,
                    "longitude": cust_lng,
                    "delivery_instructions": instructions
                },
                "items": items_data,
                "items_count": len(items_list),
                "total_amount": order.total_amount,
                "payment_method": order.payment_method if hasattr(order, "payment_method") and order.payment_method else "UPI",
                "payment_status": order.payment_status or "Paid"
            })
    return {"delivery_requests": requests}

@router.get("/api/v1/rider/notifications")
def get_rider_notifications(current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    """Returns active delivery notifications with customer GPS coordinates for the rider."""
    rider_id = current_rider.id
    
    # Active orders for this rider or newly placed available delivery orders
    recent_orders = db.query(Order).filter(
        Order.order_type == "DELIVERY",
        Order.status.in_(["PENDING", "CONFIRMED", "PREPARING", "READY"])
    ).order_by(Order.created_at.desc()).limit(15).all()
    
    notifications = []
    for order in recent_orders:
        snap = order.delivery_address_snapshot or {}
        cust_lat = snap.get("latitude")
        cust_lng = snap.get("longitude")
        cust_addr = snap.get("full_address", "Customer Address")
        
        notifications.append({
            "id": f"notif_{order.id}",
            "order_id": order.id,
            "display_id": f"ORD-{str(order.id).zfill(6)}",
            "type": "NEW_DELIVERY_ORDER",
            "title": f"New Delivery Order #ORD-{str(order.id).zfill(6)}",
            "message": f"Order worth Rs.{order.total_amount:.0f} to be delivered to {cust_addr}",
            "created_at": order.created_at,
            "customer_location": {
                "latitude": cust_lat,
                "longitude": cust_lng,
                "address": cust_addr,
                "phone": order.customer_phone,
                "instructions": order.delivery_instructions
            },
            "total_amount": order.total_amount,
            "status": order.status
        })
        
    return {"notifications": notifications}

@router.get("/api/v1/rider/deliveries/current")
def get_current_delivery(current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status.notin_(["DELIVERED", "REJECTED", "CANCELLED", "FAILED"])
    ).first()
    
    if not assignment:
        return None
        
    order = db.query(Order).filter(Order.id == assignment.order_id).first()
    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first() if order else None
    
    snap = order.delivery_address_snapshot if order else {}
    return {
        "assignment_id": assignment.id,
        "order_id": assignment.order_id,
        "display_id": f"ORD-{str(assignment.order_id).zfill(6)}",
        "status": assignment.status,
        "assigned_at": assignment.assigned_at,
        "restaurant": {
            "id": restaurant.id if restaurant else 1,
            "name": restaurant.name if restaurant else "Restaurant",
            "address": restaurant.address if restaurant else "",
            "latitude": restaurant.latitude if restaurant and restaurant.latitude else 13.0418,
            "longitude": restaurant.longitude if restaurant and restaurant.longitude else 80.2341
        },
        "customer": {
            "phone": order.customer_phone if order else None,
            "delivery_address": snap,
            "latitude": snap.get("latitude") if snap else None,
            "longitude": snap.get("longitude") if snap else None,
            "instructions": order.delivery_instructions if order else None
        }
    }

@router.get("/api/v1/riders/me")
def get_rider_me(current_rider = Depends(get_current_rider)):
    return {
        "id": current_rider.id,
        "partner_id": current_rider.id,
        "user_id": current_rider.id,
        "name": current_rider.name,
        "phone": current_rider.phone,
        "email": current_rider.email,
        "profile_image": current_rider.profile_image,
        "vehicle_type": current_rider.vehicle_type,
        "vehicle_number": current_rider.vehicle_number,
        "rating": current_rider.rating,
        "is_online": current_rider.is_online,
        "is_available": current_rider.is_available,
        "is_active": current_rider.is_active
    }

class RiderUpdatePayload(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None

@router.patch("/api/v1/riders/me")
def patch_rider_me(payload: RiderUpdatePayload, current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    if payload.name:
        current_rider.name = payload.name
    if payload.email:
        current_rider.email = payload.email
    db.commit()
    return {"success": True}

@router.get("/api/v1/riders/me/documents")
def get_rider_documents(current_rider = Depends(get_current_rider)):
    return {"documents": []}

@router.post("/api/v1/riders/me/documents")
def post_rider_documents(current_rider = Depends(get_current_rider)):
    return {"success": True}

@router.get("/api/v1/riders/me/vehicle")
def get_rider_vehicle(current_rider = Depends(get_current_rider)):
    return {
        "vehicle_type": current_rider.vehicle_type,
        "vehicle_number": current_rider.vehicle_number
    }

class RiderVehiclePayload(BaseModel):
    vehicle_type: Optional[str] = None
    vehicle_number: Optional[str] = None

@router.patch("/api/v1/riders/me/vehicle")
def patch_rider_vehicle(payload: RiderVehiclePayload, current_rider = Depends(get_current_rider), db: Session = Depends(get_db)):
    if payload.vehicle_type:
        current_rider.vehicle_type = payload.vehicle_type
    if payload.vehicle_number:
        current_rider.vehicle_number = payload.vehicle_number
    db.commit()
    return {"success": True}

@router.get("/api/v1/riders/me/bank-account")
def get_rider_bank_account(current_rider = Depends(get_current_rider)):
    return {"bank_account": None}

@router.put("/api/v1/riders/me/bank-account")
def put_rider_bank_account(current_rider = Depends(get_current_rider)):
    return {"success": True}

@router.get("/api/v1/riders/me/support-tickets")
def get_rider_support_tickets(current_rider = Depends(get_current_rider)):
    return {"support_tickets": []}

@router.post("/api/v1/riders/me/support-tickets")
def post_rider_support_tickets(current_rider = Depends(get_current_rider)):
    return {"success": True}


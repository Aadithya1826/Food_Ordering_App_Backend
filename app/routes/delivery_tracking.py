from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.order import Order
from ..models.delivery import DeliveryPartner, DeliveryAssignment, RiderLocation, DeliveryStatusHistory
from ..models.restaurant import Restaurant
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

router = APIRouter(tags=["Delivery Tracking"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class LocationPayload(BaseModel):
    order_id: Optional[int] = None
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None

class RiderStatusPayload(BaseModel):
    is_online: bool

@router.put("/api/v1/riders/{rider_id}/status")
def update_rider_status(rider_id: int, payload: RiderStatusPayload, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
        
    rider.is_online = payload.is_online
    db.commit()
    
    return {"success": True, "is_online": rider.is_online}

@router.post("/api/v1/riders/{rider_id}/location")
def update_rider_location(rider_id: int, payload: LocationPayload, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
        
    if payload.order_id:
        assignment = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.order_id == payload.order_id,
            DeliveryAssignment.rider_id == rider_id,
            DeliveryAssignment.status.notin_(["DELIVERED", "FAILED", "CANCELLED", "REJECTED"])
        ).first()
        if not assignment:
            raise HTTPException(status_code=400, detail="Rider is not assigned to this active order")
            
    # Update rider's current location
    rider.current_latitude = payload.latitude
    rider.current_longitude = payload.longitude
    rider.last_location_at = datetime.utcnow()
    
    # Insert history point
    location = RiderLocation(
        rider_id=rider_id,
        order_id=payload.order_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy=payload.accuracy,
        speed=payload.speed,
        heading=payload.heading,
        recorded_at=datetime.utcnow()
    )
    db.add(location)
    db.commit()
    return {"success": True}

@router.get("/api/v1/riders/{rider_id}/location")
def get_rider_location(rider_id: int, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    
    return {
        "latitude": rider.current_latitude,
        "longitude": rider.current_longitude,
        "updated_at": rider.last_location_at
    }

@router.get("/api/v1/public/orders/{order_id}/rider-location")
def get_order_rider_location(order_id: int, db: Session = Depends(get_db)):
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id
    ).order_by(DeliveryAssignment.id.desc()).first()
    
    if not assignment or not assignment.rider_id:
        raise HTTPException(status_code=404, detail="No rider assigned to this order")
        
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == assignment.rider_id).first()
    
    # We could also get the latest from RiderLocation
    loc = db.query(RiderLocation).filter(
        RiderLocation.rider_id == assignment.rider_id,
        RiderLocation.order_id == order_id
    ).order_by(RiderLocation.id.desc()).first()
    
    if loc:
        return {
            "latitude": float(loc.latitude),
            "longitude": float(loc.longitude),
            "accuracy": float(loc.accuracy) if loc.accuracy else None,
            "speed": float(loc.speed) if loc.speed else None,
            "heading": float(loc.heading) if loc.heading else None,
            "updated_at": loc.recorded_at
        }
    else:
        return {
            "latitude": float(rider.current_latitude) if rider.current_latitude else None,
            "longitude": float(rider.current_longitude) if rider.current_longitude else None,
            "updated_at": rider.last_location_at
        }

@router.get("/api/v1/public/orders/{order_id}/tracking")
def get_order_tracking(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()

    # Rider & Location — only populated for Delivery orders with an assignment
    rider_data = None
    rider_loc_data = None

    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id
    ).order_by(DeliveryAssignment.id.desc()).first()

    if assignment and assignment.rider_id:
        rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == assignment.rider_id).first()
        if rider:
            rider_data = {
                "id": rider.id,
                "name": rider.name,
                "phone": rider.phone,
                "rating": float(rider.rating) if rider.rating else 0.0,
                "vehicle_type": rider.vehicle_type,
                "vehicle_number": rider.vehicle_number,
                "profile_image": rider.profile_image,
            }

            loc = db.query(RiderLocation).filter(
                RiderLocation.rider_id == assignment.rider_id,
                RiderLocation.order_id == order_id
            ).order_by(RiderLocation.id.desc()).first()

            if loc:
                rider_loc_data = {
                    "latitude": float(loc.latitude),
                    "longitude": float(loc.longitude),
                    "accuracy": float(loc.accuracy) if loc.accuracy else None,
                    "speed": float(loc.speed) if loc.speed else None,
                    "heading": float(loc.heading) if loc.heading else None,
                    "updated_at": loc.recorded_at.isoformat() if loc.recorded_at else None,
                }
            elif getattr(rider, "current_latitude", None):
                rider_loc_data = {
                    "latitude": float(rider.current_latitude),
                    "longitude": float(rider.current_longitude),
                    "updated_at": rider.last_location_at.isoformat() if rider.last_location_at else None,
                }

    # Timeline — empty for non-delivery orders
    timeline_records = db.query(DeliveryStatusHistory).filter(
        DeliveryStatusHistory.order_id == order_id
    ).order_by(DeliveryStatusHistory.created_at.asc()).all()

    timeline = [
        {"status": r.status, "created_at": r.created_at.isoformat()}
        for r in timeline_records
    ]

    # Delivery address — gracefully handle Order models that don't have these columns yet
    address_data = None
    delivery_address_snapshot = getattr(order, "delivery_address_snapshot", None)
    if delivery_address_snapshot:
        snap = delivery_address_snapshot
        address_data = {
            "id": getattr(order, "delivery_address_id", None),
            "full_address": snap.get("full_address", getattr(order, "delivery_address", None)),
            "latitude": snap.get("latitude"),
            "longitude": snap.get("longitude"),
        }

    # delivery_status is an extended field; fall back to order.status for non-delivery orders
    delivery_status = getattr(order, "delivery_status", None) or order.status

    return {
        "order_id": order.id,
        "order_status": order.status,
        "delivery_status": delivery_status,
        "restaurant": {
            "id": restaurant.id if restaurant else None,
            "name": restaurant.name if restaurant else "Restaurant",
            "latitude": 13.0000000,  # Placeholder — Restaurant table has no lat/lng yet
            "longitude": 80.0000000,
        },
        "delivery_address": address_data,
        "rider": rider_data,
        "rider_location": rider_loc_data,
        "timeline": timeline,
    }

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.order import Order
from ..models.delivery import DeliveryAssignment
from ..services.delivery_status import update_delivery_status
from ..utils.dependencies import get_current_rider
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(tags=["Delivery Assignments"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class AssignmentCreate(BaseModel):
    order_id: int
    rider_id: int

@router.post("/api/v1/delivery/assignments")
def create_assignment(payload: AssignmentCreate, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == payload.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
        
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == payload.order_id,
        DeliveryAssignment.status == "UNASSIGNED"
    ).first()
    
    if assignment:
        assignment.rider_id = payload.rider_id
        assignment.status = "PENDING"
        assignment.assigned_at = datetime.utcnow()
    else:
        assignment = DeliveryAssignment(
            order_id=payload.order_id,
            rider_id=payload.rider_id,
            status="PENDING",
            assigned_at=datetime.utcnow()
        )
        db.add(assignment)
        
    db.commit()
    db.refresh(assignment)
    
    # Use the service to update order and history, transitioning from PENDING to ASSIGNED
    update_delivery_status(db, assignment.id, "ASSIGNED", "Initial Assignment")
    return assignment

@router.get("/api/v1/delivery/orders/available")
def get_available_orders(db: Session = Depends(get_db)):
    # Find orders of type Delivery that are CONFIRMED or PREPARING
    # and do NOT have an active DeliveryAssignment
    active_assignments = db.query(DeliveryAssignment.order_id).filter(
        DeliveryAssignment.status.notin_(["CANCELLED", "FAILED", "REJECTED", "UNASSIGNED"])
    ).subquery()
    
    available_orders = db.query(Order).filter(
        Order.order_type == "DELIVERY",
        Order.status.in_(["PENDING", "CONFIRMED", "PREPARING"]),
        ~Order.id.in_(active_assignments)
    ).order_by(Order.created_at.desc()).all()
    
    result = []
    for o in available_orders:
        result.append({
            "id": o.id,
            "display_id": f"ORD-{str(o.id).zfill(6)}",
            "total_amount": o.total_amount,
            "status": o.status,
            "customer_phone": o.customer_phone,
            "created_at": o.created_at,
            "delivery_address": o.delivery_address_snapshot
        })
    return result


@router.get("/api/v1/delivery/orders/{order_id}/assignment")
def get_assignment(order_id: int, db: Session = Depends(get_db)):
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id
    ).order_by(DeliveryAssignment.id.desc()).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="No assignment found for this order")
    return assignment

def check_rider_ownership(db: Session, assignment_id: int, rider_id: int):
    assignment = db.query(DeliveryAssignment).filter(DeliveryAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment.rider_id != rider_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this assignment")

@router.post("/api/v1/delivery/assignments/{assignment_id}/accept")
def accept_assignment(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "ACCEPTED")

@router.post("/api/v1/delivery/assignments/{assignment_id}/reject")
def reject_assignment(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "REJECTED")

@router.post("/api/v1/delivery/assignments/{assignment_id}/going-to-restaurant")
def going_to_restaurant(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "GOING_TO_RESTAURANT")

@router.post("/api/v1/delivery/assignments/{assignment_id}/arrived-restaurant")
def arrived_restaurant(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "ARRIVED_AT_RESTAURANT")

@router.post("/api/v1/delivery/assignments/{assignment_id}/pickup")
def pickup(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "PICKED_UP")

@router.post("/api/v1/delivery/assignments/{assignment_id}/arrived-customer")
def arrived_customer(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "ARRIVED_AT_CUSTOMER")

@router.post("/api/v1/delivery/assignments/{assignment_id}/delivered")
def delivered(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "DELIVERED")

@router.post("/api/v1/delivery/assignments/{assignment_id}/failed")
def failed(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "FAILED")

@router.post("/api/v1/delivery/assignments/{assignment_id}/cancel")
def cancel(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "CANCELLED")

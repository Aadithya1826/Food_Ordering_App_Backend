# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.order import Order
from ..models.delivery import DeliveryAssignment
from ..services.delivery_status import update_delivery_status
from ..utils.dependencies import get_current_rider
# pyrefly: ignore [missing-import]
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
    from ..models.restaurant import Restaurant
    # Find orders of type Delivery that are CONFIRMED or PREPARING
    # and do NOT have an active DeliveryAssignment
    active_assignments = db.query(DeliveryAssignment.order_id).filter(
        DeliveryAssignment.status.notin_(["CANCELLED", "FAILED", "REJECTED", "UNASSIGNED"])
    ).subquery()
    
    available_orders = db.query(Order).filter(
        Order.order_type.in_(["DELIVERY", "Delivery"]),
        Order.status.in_(["PENDING", "CONFIRMED", "PREPARING", "READY"]),
        ~Order.id.in_(active_assignments)
    ).order_by(Order.created_at.desc()).all()
    
    result = []
    for o in available_orders:
        snap = o.delivery_address_snapshot or {}
        rest = db.query(Restaurant).filter(Restaurant.id == o.restaurant_id).first()
        result.append({
            "id": o.id,
            "display_id": f"ORD-{str(o.id).zfill(6)}",
            "total_amount": o.total_amount,
            "status": o.status,
            "customer_phone": o.customer_phone,
            "created_at": o.created_at,
            "delivery_address": snap,
            "customer": {
                "name": snap.get("contact_name", "Customer"),
                "phone": snap.get("contact_phone", o.customer_phone),
                "address": snap.get("full_address", "Customer Delivery Address"),
                "latitude": snap.get("latitude"),
                "longitude": snap.get("longitude"),
                "delivery_instructions": o.delivery_instructions or snap.get("delivery_instructions")
            },
            "restaurant": {
                "id": rest.id if rest else 1,
                "name": rest.name if rest else "Restaurant",
                "address": rest.address if rest else "",
                "latitude": rest.latitude if rest and rest.latitude else 13.0418,
                "longitude": rest.longitude if rest and rest.longitude else 80.2341
            },
            "latitude": snap.get("latitude"),
            "longitude": snap.get("longitude"),
            "delivery_instructions": o.delivery_instructions
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

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional

class DeliverPayload(BaseModel):
    distance_km: Optional[float] = None

@router.post("/api/v1/delivery/assignments/{assignment_id}/delivered")
def delivered(assignment_id: int, payload: DeliverPayload = None, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    distance_km = payload.distance_km if payload else None
    return update_delivery_status(db, assignment_id, "DELIVERED", distance_km=distance_km)

@router.post("/api/v1/delivery/assignments/{assignment_id}/failed")
def failed(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "FAILED")

@router.post("/api/v1/delivery/assignments/{assignment_id}/cancel")
def cancel(assignment_id: int, current_rider: dict = Depends(get_current_rider), db: Session = Depends(get_db)):
    check_rider_ownership(db, assignment_id, current_rider.id)
    return update_delivery_status(db, assignment_id, "CANCELLED")


# ──────────────────────────────────────────────────────────────────────────────
# Supervisor / Admin Third-Party Partner Assignment & Status Override
# ──────────────────────────────────────────────────────────────────────────────

class ThirdPartyPartnerPayload(BaseModel):
    partner: str
    rider_name: str
    rider_phone: str
    vehicle_type: Optional[str] = "Bike"
    vehicle_number: Optional[str] = None
    eta: Optional[str] = "25 mins"
    tracking_url: Optional[str] = None
    otp: Optional[str] = "4821"

@router.post("/api/v1/delivery/orders/{order_id}/assign-partner")
def assign_third_party_partner(order_id: int, payload: ThirdPartyPartnerPayload, db: Session = Depends(get_db)):
    from ..models.delivery import DeliveryPartner, DeliveryStatusHistory
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    clean_phone = payload.rider_phone.replace("+91", "").strip()
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.phone.like(f"%{clean_phone}%")).first()
    if not rider:
        rider = DeliveryPartner(
            name=payload.rider_name,
            phone=payload.rider_phone,
            vehicle_type=payload.vehicle_type or "Bike",
            vehicle_number=payload.vehicle_number or "TN-01-EXP-001",
            is_online=True,
            is_active=True,
            is_available=False
        )
        db.add(rider)
        db.flush()
    else:
        rider.name = payload.rider_name
        rider.vehicle_type = payload.vehicle_type or rider.vehicle_type
        if payload.vehicle_number:
            rider.vehicle_number = payload.vehicle_number
        db.flush()

    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id
    ).order_by(DeliveryAssignment.id.desc()).first()

    now = datetime.utcnow()
    if assignment:
        assignment.rider_id = rider.id
        assignment.delivery_partner = payload.partner
        assignment.estimated_delivery_time = payload.eta
        assignment.tracking_url = payload.tracking_url
        assignment.status = "OUT_FOR_DELIVERY"
        assignment.assigned_at = now
        assignment.accepted_at = now
        assignment.picked_up_at = now
    else:
        assignment = DeliveryAssignment(
            order_id=order_id,
            rider_id=rider.id,
            delivery_partner=payload.partner,
            estimated_delivery_time=payload.eta,
            tracking_url=payload.tracking_url,
            status="OUT_FOR_DELIVERY",
            assigned_at=now,
            accepted_at=now,
            picked_up_at=now
        )
        db.add(assignment)
        db.flush()

    order.delivery_status = "OUT_FOR_DELIVERY"
    order.status = "READY"
    order.updated_at = now

    otp_str = payload.otp or "4821"
    history = DeliveryStatusHistory(
        order_id=order_id,
        delivery_assignment_id=assignment.id,
        rider_id=rider.id,
        status="OUT_FOR_DELIVERY",
        notes=f"Assigned to {payload.partner} rider {payload.rider_name} ({payload.rider_phone}). OTP: {otp_str}. ETA: {payload.eta or '25 mins'}. Order is Out for Delivery."
    )
    db.add(history)
    db.commit()
    db.refresh(assignment)

    return {
        "success": True,
        "message": f"Successfully assigned {payload.partner} rider {payload.rider_name}",
        "assignment_id": assignment.id,
        "order_id": order.id,
        "partner": payload.partner,
        "rider_name": payload.rider_name,
        "rider_phone": payload.rider_phone,
        "vehicle_type": payload.vehicle_type,
        "vehicle_number": payload.vehicle_number,
        "eta": payload.eta,
        "otp": otp_str,
        "status": assignment.status
    }


class UpdateDeliveryStatusPayload(BaseModel):
    status: str
    notes: Optional[str] = None
    otp: Optional[str] = None

@router.post("/api/v1/delivery/orders/{order_id}/status")
def update_order_delivery_status(order_id: int, payload: UpdateDeliveryStatusPayload, db: Session = Depends(get_db)):
    from ..models.delivery import DeliveryStatusHistory
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id
    ).order_by(DeliveryAssignment.id.desc()).first()

    norm_status = payload.status.upper().replace(" ", "_")
    now = datetime.utcnow()

    # Third-Party Delivery Status Logic
    if norm_status in ["DELIVERED", "COMPLETED"]:
        order.delivery_status = "DELIVERED"
        order.status = "COMPLETED"
        order.updated_at = now

        # Cash on Delivery (COD) Auto-Settlement
        if order.payment_method in ["Cash", "COD"] and order.payment_status != "Paid":
            order.payment_status = "Paid"

        if assignment:
            assignment.status = "DELIVERED"
            assignment.delivered_at = now

        # Auto-credit loyalty points (idempotent)
        try:
            from ..models.customer import Customer, LoyaltyTransaction
            if order.customer_phone:
                from ..utils.phone import normalize_phone
                norm_phone = normalize_phone(order.customer_phone)
                cust = db.query(Customer).filter(Customer.phone == norm_phone).first() or db.query(Customer).filter(Customer.phone == order.customer_phone).first()
                if cust:
                    existing = db.query(LoyaltyTransaction).filter(
                        LoyaltyTransaction.order_id == order.id,
                        LoyaltyTransaction.transaction_type == "ORDER_CREDIT"
                    ).first()
                    if not existing:
                        RUPEES_PER_POINT = 10
                        base_amt = (order.total_amount or 0) - (order.tip_amount or 0) - (order.delivery_fee or 0)
                        pts = max(0, int(base_amt // RUPEES_PER_POINT))
                        if pts > 0:
                            tx = LoyaltyTransaction(
                                customer_id=cust.id,
                                order_id=order.id,
                                transaction_type="ORDER_CREDIT",
                                points=pts,
                                notes=f"Points earned for delivery order #{order.id}",
                                created_at=now
                            )
                            db.add(tx)
                            cust.loyalty_points = (cust.loyalty_points or 0) + pts
        except Exception as e:
            print("[Loyalty Auto-Credit Error]:", e)

    elif norm_status in ["OUT_FOR_DELIVERY", "PICKED_UP"]:
        order.delivery_status = "OUT_FOR_DELIVERY"
        order.updated_at = now
        if assignment:
            assignment.status = "OUT_FOR_DELIVERY"
            assignment.picked_up_at = now
    else:
        order.delivery_status = norm_status
        order.updated_at = now
        if assignment:
            assignment.status = norm_status

    history = DeliveryStatusHistory(
        order_id=order_id,
        delivery_assignment_id=assignment.id if assignment else None,
        rider_id=assignment.rider_id if assignment else None,
        status=norm_status,
        notes=payload.notes or f"Third-party delivery status updated to {norm_status}"
    )
    db.add(history)
    db.commit()

    return {
        "success": True,
        "order_id": order_id,
        "status": order.status,
        "delivery_status": order.delivery_status,
        "payment_status": order.payment_status
    }

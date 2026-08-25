from sqlalchemy.orm import Session
from fastapi import HTTPException
from datetime import datetime
from ..models.order import Order
from ..models.delivery import DeliveryAssignment, DeliveryStatusHistory

def update_delivery_status(db: Session, assignment_id: int, new_status: str, notes: str = None):
    assignment = db.query(DeliveryAssignment).filter(DeliveryAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Delivery assignment not found")
        
    order = db.query(Order).filter(Order.id == assignment.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Mapping of assignment status to order.delivery_status
    status_mapping = {
        "ASSIGNED": "RIDER_ASSIGNED",
        "ACCEPTED": "RIDER_ACCEPTED",
        "GOING_TO_RESTAURANT": "RIDER_GOING_TO_RESTAURANT",
        "ARRIVED_AT_RESTAURANT": "RIDER_ARRIVED_AT_RESTAURANT",
        "PICKED_UP": "OUT_FOR_DELIVERY",
        "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY",
        "ARRIVED_AT_CUSTOMER": "RIDER_ARRIVED",
        "DELIVERED": "DELIVERED",
        "REJECTED": "RIDER_SEARCHING",
        "FAILED": "DELIVERY_FAILED",
        "CANCELLED": "CANCELLED"
    }
    
    if new_status not in status_mapping:
        raise HTTPException(status_code=400, detail="Invalid status")
        
    order_delivery_status = status_mapping[new_status]
    now = datetime.utcnow()
    
    try:
        # Update Assignment
        assignment.status = new_status
        if new_status == "ACCEPTED":
            assignment.accepted_at = now
        elif new_status == "REJECTED":
            assignment.rejected_at = now
        elif new_status == "ARRIVED_AT_RESTAURANT":
            assignment.arrived_restaurant_at = now
        elif new_status == "PICKED_UP" or new_status == "OUT_FOR_DELIVERY":
            assignment.picked_up_at = now
        elif new_status == "ARRIVED_AT_CUSTOMER":
            assignment.arrived_customer_at = now
        elif new_status == "DELIVERED":
            assignment.delivered_at = now
            order.status = "COMPLETED" # Finalize the restaurant order status
            
        # Update Order
        order.delivery_status = order_delivery_status
        
        # Insert History
        history = DeliveryStatusHistory(
            order_id=order.id,
            delivery_assignment_id=assignment.id,
            rider_id=assignment.rider_id,
            status=new_status,
            notes=notes,
            created_at=now
        )
        db.add(history)
        
        # Commit Transaction
        db.commit()
        db.refresh(assignment)
        db.refresh(order)
        return assignment
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

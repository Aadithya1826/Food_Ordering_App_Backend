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

    # State Transition Validation
    valid_transitions = {
        "PENDING": ["ASSIGNED", "CANCELLED"],
        "ASSIGNED": ["ACCEPTED", "REJECTED", "CANCELLED"],
        "ACCEPTED": ["GOING_TO_RESTAURANT", "CANCELLED"],
        "GOING_TO_RESTAURANT": ["ARRIVED_AT_RESTAURANT", "CANCELLED"],
        "ARRIVED_AT_RESTAURANT": ["PICKED_UP", "CANCELLED"],
        "PICKED_UP": ["OUT_FOR_DELIVERY", "ARRIVED_AT_CUSTOMER", "CANCELLED"],
        "OUT_FOR_DELIVERY": ["ARRIVED_AT_CUSTOMER", "CANCELLED"],
        "ARRIVED_AT_CUSTOMER": ["DELIVERED", "FAILED", "CANCELLED"],
        "DELIVERED": [],
        "REJECTED": [],
        "FAILED": [],
        "CANCELLED": []
    }
    
    current_status = assignment.status or "PENDING"
    
    if current_status == "DELIVERED" and new_status == "DELIVERED":
        raise HTTPException(status_code=400, detail="Delivery already completed")
        

    if current_status in valid_transitions and new_status not in valid_transitions[current_status]:
        raise HTTPException(status_code=400, detail=f"Invalid state transition from {current_status} to {new_status}")

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
        from ..models.delivery import DeliveryPartner
        rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == assignment.rider_id).first()
        
        if new_status == "ACCEPTED" and rider:
            rider.is_available = False
            
        if new_status == "DELIVERED" and current_status != "DELIVERED":
            # Increment total rides exactly once
            if rider:
                rider.total_rides = (rider.total_rides or 0) + 1
                rider.is_available = True
                
        if new_status in ["REJECTED", "FAILED", "CANCELLED"] and rider:
            rider.is_available = True
                
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

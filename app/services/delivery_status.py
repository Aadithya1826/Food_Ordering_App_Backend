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
        "ASSIGNED": ["ACCEPTED", "REJECTED", "CANCELLED", "GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT", "PICKED_UP"],
        "ACCEPTED": ["GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT", "PICKED_UP", "CANCELLED"],
        "GOING_TO_RESTAURANT": ["ARRIVED_AT_RESTAURANT", "PICKED_UP", "CANCELLED"],
        "ARRIVED_AT_RESTAURANT": ["PICKED_UP", "CANCELLED"],
        "PICKED_UP": ["OUT_FOR_DELIVERY", "ARRIVED_AT_CUSTOMER", "DELIVERED", "CANCELLED"],
        "OUT_FOR_DELIVERY": ["ARRIVED_AT_CUSTOMER", "DELIVERED", "CANCELLED"],
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
        "ACCEPTED": "RIDER_ASSIGNED",
        "GOING_TO_RESTAURANT": "RIDER_GOING_TO_RESTAURANT",
        "ARRIVED_AT_RESTAURANT": "RIDER_ARRIVED_AT_RESTAURANT",
        "PICKED_UP": "PICKED_UP",
        "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY",
        "ARRIVED_AT_CUSTOMER": "RIDER_ARRIVED",
        "DELIVERED": "DELIVERED",
        "REJECTED": "RIDER_SEARCHING",
        "FAILED": "DELIVERY_FAILED",
        "CANCELLED": "CANCELLED"
    }

    # Mapping of assignment status to DeliveryStatusHistory.status
    history_status_mapping = {
        "ASSIGNED": "RIDER_ASSIGNED",
        "ACCEPTED": "RIDER_ACCEPTED",
        "GOING_TO_RESTAURANT": "GOING_TO_RESTAURANT",
        "ARRIVED_AT_RESTAURANT": "ARRIVED_AT_RESTAURANT",
        "PICKED_UP": "PICKED_UP",
        "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY",
        "ARRIVED_AT_CUSTOMER": "ARRIVED_AT_CUSTOMER",
        "DELIVERED": "DELIVERED",
        "REJECTED": "RIDER_REJECTED",
        "FAILED": "DELIVERY_FAILED",
        "CANCELLED": "CANCELLED"
    }
    
    if new_status not in status_mapping:
        raise HTTPException(status_code=400, detail="Invalid status")
        
    order_delivery_status = status_mapping[new_status]
    hist_status = history_status_mapping.get(new_status, "RIDER_ASSIGNED")
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
            order.status = "COMPLETED"  # Finalize the restaurant order status
            # ── Loyalty auto-credit (backend-only, idempotent) ──────────────────
            try:
                from ..models.customer import Customer, LoyaltyTransaction
                if order.customer_phone and order.payment_status == "Paid":
                    from ..utils.phone import normalize_phone
                    norm_phone = normalize_phone(order.customer_phone)
                    cust = db.query(Customer).filter(Customer.phone == norm_phone).first()
                    # Also try with the raw phone in case it was stored differently
                    if not cust:
                        cust = db.query(Customer).filter(Customer.phone == order.customer_phone).first()
                    if cust:
                        # 1 point per ₹10 of total_amount (excluding tip & delivery fee)
                        RUPEES_PER_POINT = 10
                        base_amount = (order.total_amount or 0) - (order.tip_amount or 0) - (order.delivery_fee or 0)
                        points_to_award = max(0, int(base_amount // RUPEES_PER_POINT))
                        if points_to_award > 0:
                            # Insert only if no prior ORDER_CREDIT row for this order (idempotency)
                            existing = db.query(LoyaltyTransaction).filter(
                                LoyaltyTransaction.order_id == order.id,
                                LoyaltyTransaction.transaction_type == "ORDER_CREDIT"
                            ).first()
                            if not existing:
                                lt = LoyaltyTransaction(
                                    customer_id=cust.id,
                                    order_id=order.id,
                                    points=points_to_award,
                                    transaction_type="ORDER_CREDIT",
                                    description=f"Delivery order ORD-{str(order.id).zfill(6)} completed"
                                )
                                db.add(lt)
                                cust.loyalty_points = (cust.loyalty_points or 0) + points_to_award
            except Exception as _loyalty_err:
                # Loyalty failure must NOT roll back the delivery completion
                import logging as _log
                _log.getLogger(__name__).warning(f"Loyalty credit failed for order {order.id}: {_loyalty_err}")

            
        # Update Order
        order.delivery_status = order_delivery_status
        
        # Insert History
        history = DeliveryStatusHistory(
            order_id=order.id,
            delivery_assignment_id=assignment.id,
            rider_id=assignment.rider_id,
            status=hist_status,
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


def sync_order_delivery_on_status_change(db: Session, order: Order, new_restaurant_status: str):
    """
    Sync delivery assignment and delivery_status when the kitchen / restaurant
    moves a delivery order to PREPARING, READY, CANCELLED, etc.
    """
    if not order or (order.order_type or "").upper() != "DELIVERY":
        return

    from ..models.delivery import DeliveryAssignment, DeliveryPartner, DeliveryStatusHistory

    now = datetime.utcnow()
    new_status_upper = (new_restaurant_status or "").upper()

    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order.id
    ).order_by(DeliveryAssignment.id.desc()).first()

    history_status_mapping = {
        "ASSIGNED": "RIDER_ASSIGNED",
        "ACCEPTED": "RIDER_ACCEPTED",
        "GOING_TO_RESTAURANT": "GOING_TO_RESTAURANT",
        "ARRIVED_AT_RESTAURANT": "ARRIVED_AT_RESTAURANT",
        "PICKED_UP": "PICKED_UP",
        "OUT_FOR_DELIVERY": "OUT_FOR_DELIVERY",
        "ARRIVED_AT_CUSTOMER": "ARRIVED_AT_CUSTOMER",
        "DELIVERED": "DELIVERED",
        "REJECTED": "RIDER_REJECTED",
        "FAILED": "DELIVERY_FAILED",
        "CANCELLED": "CANCELLED",
        "RIDER_SEARCHING": "RIDER_SEARCHING",
        "RIDER_ASSIGNED": "RIDER_ASSIGNED",
    }

    if new_status_upper == "PREPARING":
        if not assignment or assignment.status in ["CANCELLED", "REJECTED", "FAILED"]:
            # Try to find an online, available rider to assign immediately
            rider = db.query(DeliveryPartner).filter(
                DeliveryPartner.is_online == True,
                DeliveryPartner.is_available == True,
                DeliveryPartner.is_active == True
            ).first()

            if rider:
                assignment = DeliveryAssignment(
                    order_id=order.id,
                    rider_id=rider.id,
                    status="ASSIGNED",
                    assigned_at=now
                )
                db.add(assignment)
                db.flush()
                order.delivery_status = "RIDER_ASSIGNED"

                history = DeliveryStatusHistory(
                    order_id=order.id,
                    delivery_assignment_id=assignment.id,
                    rider_id=rider.id,
                    status="RIDER_ASSIGNED",
                    notes="Order moved to PREPARING; rider assigned.",
                    created_at=now
                )
                db.add(history)
            else:
                order.delivery_status = "RIDER_SEARCHING"
                history = DeliveryStatusHistory(
                    order_id=order.id,
                    status="RIDER_SEARCHING",
                    notes="Order moved to PREPARING; searching for rider.",
                    created_at=now
                )
                db.add(history)
        else:
            # Assignment exists
            if assignment.status == "PENDING" or assignment.status == "ASSIGNED":
                assignment.status = "ASSIGNED"
                order.delivery_status = "RIDER_ASSIGNED"
            assignment.updated_at = now

            hist_status = history_status_mapping.get(assignment.status, "RIDER_ASSIGNED")
            history = DeliveryStatusHistory(
                order_id=order.id,
                delivery_assignment_id=assignment.id,
                rider_id=assignment.rider_id,
                status=hist_status,
                notes="Kitchen started PREPARING the order",
                created_at=now
            )
            db.add(history)

    elif new_status_upper == "READY":
        if assignment and assignment.status not in ["CANCELLED", "REJECTED", "FAILED", "DELIVERED"]:
            if assignment.status == "PENDING" or assignment.status == "ASSIGNED":
                assignment.status = "ASSIGNED"
                order.delivery_status = "RIDER_ASSIGNED"
            assignment.updated_at = now

            hist_status = history_status_mapping.get(assignment.status, "RIDER_ASSIGNED")
            history = DeliveryStatusHistory(
                order_id=order.id,
                delivery_assignment_id=assignment.id,
                rider_id=assignment.rider_id,
                status=hist_status,
                notes="Food is READY for pickup at restaurant",
                created_at=now
            )
            db.add(history)

    elif new_status_upper == "CANCELLED":
        if assignment and assignment.status not in ["CANCELLED", "DELIVERED"]:
            assignment.status = "CANCELLED"
            assignment.updated_at = now
            order.delivery_status = "CANCELLED"

            if assignment.rider_id:
                rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == assignment.rider_id).first()
                if rider:
                    rider.is_available = True

            history = DeliveryStatusHistory(
                order_id=order.id,
                delivery_assignment_id=assignment.id,
                rider_id=assignment.rider_id,
                status="CANCELLED",
                notes="Order cancelled by restaurant",
                created_at=now
            )
            db.add(history)



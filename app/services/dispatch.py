# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import math
import json
from ..models.delivery import DeliveryPartner, DeliveryAssignment, DeliveryStatusHistory
from ..models.order import Order
from ..models.restaurant import Restaurant

MAX_ACTIVE_ORDERS_PER_RIDER = 2
RESTAURANT_NEARBY_RADIUS_METERS = 500

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    """
    if None in (lat1, lon1, lat2, lon2):
        return float('inf')
        
    try:
        lon1, lat1, lon2, lat2 = map(math.radians, [float(lon1), float(lat1), float(lon2), float(lat2)])
    except (ValueError, TypeError):
        return float('inf')
        
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 6371 # Radius of earth in kilometers.
    return c * r

def get_eta_minutes(distance_km):
    return max(1, round((distance_km / 20.0) * 60))

def get_customer_coords(order: Order):
    raw = order.delivery_address_snapshot
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not raw:
        raw = {}
    lat = raw.get("latitude") or raw.get("lat")
    lng = raw.get("longitude") or raw.get("lng")
    return lat, lng

def check_batch_feasibility(db: Session, rider: DeliveryPartner, active_assignment: DeliveryAssignment, new_order: Order, restaurant: Restaurant) -> bool:
    """
    Evaluates whether the rider can take the new_order as a batch.
    """
    active_order = db.query(Order).filter(Order.id == active_assignment.order_id).first()
    if not active_order:
        return False
        
    # Must be same restaurant
    if active_order.restaurant_id != new_order.restaurant_id:
        return False
        
    latA, lngA = get_customer_coords(active_order)
    latB, lngB = get_customer_coords(new_order)
    
    if None in (latA, lngA, latB, lngB):
        return False
        
    # Detour Pre-filter: Distance between Customer A and Customer B
    dist_ab = haversine(latA, lngA, latB, lngB)
    if dist_ab > 3.0: # Max 3km detour
        return False
        
    # Check if rider is still at/near restaurant
    if active_assignment.status in ["ASSIGNED", "ACCEPTED", "GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT"]:
        return True
        
    # If out for delivery, verify they haven't travelled too far from the restaurant
    if active_assignment.status in ["PICKED_UP", "OUT_FOR_DELIVERY"]:
        dist_to_restaurant = haversine(rider.current_latitude, rider.current_longitude, restaurant.latitude, restaurant.longitude)
        if dist_to_restaurant > 0.5: # 500 meters
            return False
            
    return True

def dispatch_order(db: Session, order_id: int):
    """
    Centralized dispatch algorithm executing Normal and Batch eligibility,
    with Fairness (Proximity / Longest Idle) evaluation.
    """
    now = datetime.utcnow()
    
    # 1. Fetch Order with FOR UPDATE to prevent race conditions
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if not order or (order.order_type or "").upper() != "DELIVERY":
        return None
        
    # Do not redispatch if already assigned actively
    existing = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id,
        DeliveryAssignment.status.in_(["ASSIGNED", "ACCEPTED", "GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT", "PICKED_UP", "OUT_FOR_DELIVERY", "ARRIVED_AT_CUSTOMER"])
    ).first()
    if existing:
        return existing
        
    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
    if not restaurant:
        return None
        
    # 2. Fetch all online, active riders
    # We DO NOT filter by is_available here because busy riders (is_available=False)
    # may still be eligible for BATCHING.
    five_mins_ago = now - timedelta(minutes=5)
    riders = db.query(DeliveryPartner).filter(
        DeliveryPartner.is_online == True,
        DeliveryPartner.is_active == True,
        DeliveryPartner.current_latitude.isnot(None),
        DeliveryPartner.current_longitude.isnot(None),
        DeliveryPartner.last_location_at >= five_mins_ago
    ).all()
    
    eligible_candidates = []
    
    for rider in riders:
        # Check active assignments
        active_assignments = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.rider_id == rider.id,
            DeliveryAssignment.status.in_(["ASSIGNED", "ACCEPTED", "GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT", "PICKED_UP", "OUT_FOR_DELIVERY", "ARRIVED_AT_CUSTOMER"])
        ).all()
        
        active_count = len(active_assignments)
        
        distance_to_rest = haversine(
            rider.current_latitude, rider.current_longitude, 
            float(restaurant.latitude or 0), float(restaurant.longitude or 0)
        )
        
        if active_count == 0 and rider.is_available:
            # Normal Candidate
            eligible_candidates.append({
                "rider": rider,
                "type": "NORMAL",
                "distance": distance_to_rest,
                "idle_time": rider.last_location_at or rider.updated_at or datetime.min
            })
        elif active_count == 1 and active_count < MAX_ACTIVE_ORDERS_PER_RIDER:
            # Batch Candidate
            if check_batch_feasibility(db, rider, active_assignments[0], order, restaurant):
                eligible_candidates.append({
                    "rider": rider,
                    "type": "BATCH",
                    "distance": distance_to_rest,
                    "idle_time": rider.last_location_at or rider.updated_at or datetime.min
                })
                
    if not eligible_candidates:
        return None
        
    # 3. Fairness Ranking
    # Primary: Proximity (At Hotel < 500m vs Away)
    # Secondary: Longest idle (Oldest updated_at)
    def rank_key(candidate):
        is_at_hotel = 0 if candidate["distance"] <= (RESTAURANT_NEARBY_RADIUS_METERS / 1000.0) else 1
        return (is_at_hotel, candidate["idle_time"])
        
    eligible_candidates.sort(key=rank_key)
    
    # 4. Atomic Assignment
    for candidate in eligible_candidates:
        best_rider = candidate["rider"]
        
        locked_rider = db.query(DeliveryPartner).filter(
            DeliveryPartner.id == best_rider.id
        ).with_for_update().first()
        
        if not locked_rider or not locked_rider.is_online or not locked_rider.is_active:
            continue
            
        # Re-verify active count under lock
        active_count = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.rider_id == locked_rider.id,
            DeliveryAssignment.status.in_(["ASSIGNED", "ACCEPTED", "GOING_TO_RESTAURANT", "ARRIVED_AT_RESTAURANT", "PICKED_UP", "OUT_FOR_DELIVERY", "ARRIVED_AT_CUSTOMER"])
        ).count()
        
        if active_count >= MAX_ACTIVE_ORDERS_PER_RIDER:
            continue
            
        if active_count == 0 and not locked_rider.is_available:
            continue # Someone else assigned them
            
        # Assign!
        # If order was unassigned, maybe there's a row, maybe not.
        unassigned_record = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.order_id == order.id,
            DeliveryAssignment.status == "UNASSIGNED"
        ).first()
        
        if unassigned_record:
            assignment = unassigned_record
            assignment.rider_id = locked_rider.id
            assignment.status = "ASSIGNED"
            assignment.assigned_at = now
        else:
            assignment = DeliveryAssignment(
                order_id=order.id,
                rider_id=locked_rider.id,
                status="ASSIGNED",
                assigned_at=now
            )
            db.add(assignment)
            
        order.delivery_status = "RIDER_ASSIGNED"
        
        # NOTE: Do NOT immediately mark rider as is_available = False here.
        # The rider has only been OFFERED the assignment. Wait until ACCEPTED.
        # Wait, the previous logic in `delivery_status.py` ACCEPTED state transitions to is_available = False.
        # Wait, the old dispatch.py did `locked_rider.is_available = False` immediately.
        # We should follow old behavior if that's what's expected, but actually, 
        # setting it False prevents them from getting a Batch if they haven't accepted yet.
        # Let's set it False if it's their FIRST order to prevent normal dispatch race.
        if active_count == 0:
            locked_rider.is_available = False
            
        history = DeliveryStatusHistory(
            order_id=order.id,
            rider_id=locked_rider.id,
            status="ASSIGNED",
            notes=f"Central Dispatcher assigned rider {locked_rider.id} ({candidate['type']})",
            created_at=now
        )
        db.add(history)
        
        db.commit()
        db.refresh(assignment)
        
        history.delivery_assignment_id = assignment.id
        db.commit()
        
        return assignment

    return None

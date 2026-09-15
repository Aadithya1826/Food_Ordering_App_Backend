from sqlalchemy.orm import Session
from ..models.delivery import DeliveryPartner, DeliveryAssignment, DeliveryStatusHistory
from ..models.order import Order
from datetime import datetime, timedelta
import math

# Geometric haversine distance fallback
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0 # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_eta_minutes(distance_km):
    # Rough estimate: 20 km/h average speed in city
    return max(1, round((distance_km / 20.0) * 60))

def dispatch_order(db: Session, order_id: int):
    # Fetch the order
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if not order or order.order_type != "DELIVERY" or order.delivery_status != "RIDER_SEARCHING":
        return None
    
    # Check if order already has an active assignment
    existing_assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id,
        DeliveryAssignment.status.in_(['ASSIGNED', 'ACCEPTED', 'GOING_TO_RESTAURANT', 'ARRIVED_AT_RESTAURANT', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'ARRIVED_AT_CUSTOMER'])
    ).first()
    
    if existing_assignment:
        return existing_assignment

    # Get restaurant coordinates
    rest_lat = None
    rest_lng = None
    if order.restaurant and order.restaurant.latitude and order.restaurant.longitude:
        try:
            rest_lat = float(order.restaurant.latitude)
            rest_lng = float(order.restaurant.longitude)
        except ValueError:
            pass

    # 1. Fetch eligible riders (online, available, active)
    eligible_riders = db.query(DeliveryPartner).filter(
        DeliveryPartner.is_online == True,
        DeliveryPartner.is_available == True,
        DeliveryPartner.is_active == True,
        DeliveryPartner.current_latitude.isnot(None),
        DeliveryPartner.current_longitude.isnot(None),
    ).all()

    # 2. Filter out riders with stale GPS (> 5 mins) or active assignments
    five_mins_ago = datetime.utcnow() - timedelta(minutes=5)
    valid_riders = []

    for rider in eligible_riders:
        # Check freshness
        if not rider.last_location_at or rider.last_location_at < five_mins_ago:
            continue
        
        # Check active assignments for this rider
        active_count = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.rider_id == rider.id,
            DeliveryAssignment.status.in_(['ASSIGNED', 'ACCEPTED', 'GOING_TO_RESTAURANT', 'ARRIVED_AT_RESTAURANT', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'ARRIVED_AT_CUSTOMER'])
        ).count()
        
        if active_count > 0:
            continue
            
        # Calculate ETA
        distance = 0.0
        eta = 0
        if rest_lat is not None and rest_lng is not None and rider.current_latitude is not None and rider.current_longitude is not None:
            distance = haversine(rider.current_latitude, rider.current_longitude, rest_lat, rest_lng)
            eta = get_eta_minutes(distance)
            
        valid_riders.append({
            "rider": rider,
            "eta": eta,
            "distance": distance
        })

    if not valid_riders:
        # Leave order in RIDER_SEARCHING
        return None

    # Rank by lowest ETA
    valid_riders.sort(key=lambda x: x["eta"])

    # Try to lock and assign the best rider
    for candidate in valid_riders:
        best_rider = candidate["rider"]
        
        # Lock the rider row to prevent race conditions
        locked_rider = db.query(DeliveryPartner).filter(
            DeliveryPartner.id == best_rider.id,
            DeliveryPartner.is_online == True,
            DeliveryPartner.is_available == True,
            DeliveryPartner.is_active == True
        ).with_for_update().first()
        
        if not locked_rider:
            continue # Someone else took them, try next
            
        # Double check no active assignments inside lock
        active_count = db.query(DeliveryAssignment).filter(
            DeliveryAssignment.rider_id == locked_rider.id,
            DeliveryAssignment.status.in_(['ASSIGNED', 'ACCEPTED', 'GOING_TO_RESTAURANT', 'ARRIVED_AT_RESTAURANT', 'PICKED_UP', 'OUT_FOR_DELIVERY', 'ARRIVED_AT_CUSTOMER'])
        ).count()
        
        if active_count > 0:
            continue
            
        # We have our rider!
        assignment = DeliveryAssignment(
            order_id=order.id,
            rider_id=locked_rider.id,
            status="ASSIGNED",
            assigned_at=datetime.utcnow(),
        )
        db.add(assignment)
        
        # Mark rider busy
        locked_rider.is_available = False
        
        # Mark order assigned
        order.delivery_status = "RIDER_ASSIGNED"
        
        # Insert History
        history = DeliveryStatusHistory(
            order_id=order.id,
            rider_id=locked_rider.id,
            status="ASSIGNED",
            latitude=locked_rider.current_latitude,
            longitude=locked_rider.current_longitude,
        )
        db.add(history)
        
        db.commit()
        db.refresh(assignment)
        
        # Update history with assignment ID
        history.delivery_assignment_id = assignment.id
        db.commit()
        
        return assignment

    return None

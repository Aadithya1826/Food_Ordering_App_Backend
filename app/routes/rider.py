# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date, timedelta
from collections import defaultdict
from ..db import SessionLocal
from ..models.delivery import DeliveryPartner, DeliveryAssignment, RiderDocument, RiderBankDetail
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
    name: Optional[str] = None


@router.post("/api/v1/rider/auth/login")
def rider_login(payload: RiderLoginPayload, db: Session = Depends(get_db)):
    # Accept default OTP/passwords or bypass if empty in dev
    if payload.otp and payload.otp not in ["1234", "0000", ""]:
        if payload.password and payload.password not in ["1234", "0000", ""]:
            raise HTTPException(status_code=400, detail="Invalid OTP/Password (Use 1234)")

    raw_phone = payload.phone.strip()
    clean_digits = "".join([c for c in raw_phone if c.isdigit()])
    if len(clean_digits) > 10 and clean_digits.startswith("91"):
        clean_phone = clean_digits[2:]
    else:
        clean_phone = clean_digits[-10:] if len(clean_digits) >= 10 else clean_digits

    rider = db.query(DeliveryPartner).filter(
        (DeliveryPartner.phone == raw_phone) |
        (DeliveryPartner.phone == clean_phone) |
        (DeliveryPartner.phone == f"+91{clean_phone}") |
        (DeliveryPartner.phone.ilike(f"%{clean_phone}%"))
    ).first()

    if not rider:
        raise HTTPException(status_code=401, detail="Rider not registered. Please contact administrator.")

    active_assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider.id,
        DeliveryAssignment.status.notin_(["DELIVERED", "REJECTED", "CANCELLED", "FAILED", "ASSIGNED"])
    ).first()

    rider.is_online = True
    if active_assignment:
        rider.is_available = False
    else:
        # Only preserve or calculate availability safely
        rider.is_available = True
    db.commit()

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
            "is_online": rider.is_online,
            "is_available": rider.is_available,
            "rating": round(rider.rating or 5.0, 1),
            "total_rides": rider.total_rides or 0,
        }
    }


@router.post("/api/v1/rider/auth/logout")
def rider_logout():
    return {"message": "Rider logged out successfully"}


def format_assignment_data(assignment: DeliveryAssignment, order: Order, restaurant: Optional[Restaurant], db: Session):
    import json
    
    # Query items for the order
    items = []
    items_count = 0
    if order:
        order_items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        items_count = len(order_items)
        for oi in order_items:
            item_name = oi.menu_item.name if oi.menu_item else f"Item #{oi.menu_item_id}"
            item_price = oi.price if oi.price is not None else (oi.menu_item.price if oi.menu_item else 0.0)
            items.append({
                "id": oi.id,
                "menu_item_id": oi.menu_item_id,
                "name": item_name,
                "quantity": oi.quantity or 1,
                "price": float(item_price or 0.0),
                "total_price": round(float(item_price or 0.0) * (oi.quantity or 1), 2)
            })

    # Parse delivery_address snapshot
    raw_addr = (order.delivery_address_snapshot if order else {}) or {}
    if isinstance(raw_addr, str):
        try:
            raw_addr = json.loads(raw_addr)
        except Exception:
            raw_addr = {"address_line": raw_addr}

    customer_address_str = raw_addr.get("address_line") or raw_addr.get("full_address") or raw_addr.get("address") or (getattr(order, "delivery_address", "") if order else "") or ""
    customer_name = raw_addr.get("contact_name") or raw_addr.get("name") or getattr(order, "customer_name", None) or "Customer"
    customer_phone = raw_addr.get("contact_phone") or raw_addr.get("phone") or getattr(order, "customer_phone", None) or ""
    
    cust_lat = float(raw_addr.get("latitude")) if raw_addr.get("latitude") is not None else (float(raw_addr.get("lat")) if raw_addr.get("lat") is not None else None)
    cust_lng = float(raw_addr.get("longitude")) if raw_addr.get("longitude") is not None else (float(raw_addr.get("lng")) if raw_addr.get("lng") is not None else None)

    formatted_delivery_addr = {
        "address_line": customer_address_str,
        "full_address": customer_address_str,
        "landmark": raw_addr.get("landmark") or "",
        "instructions": (order.delivery_instructions if order else None) or raw_addr.get("instructions") or "",
        "contact_name": customer_name,
        "contact_phone": customer_phone,
        "latitude": cust_lat,
        "longitude": cust_lng,
    }

    rest_lat = float(restaurant.latitude) if restaurant and getattr(restaurant, "latitude", None) is not None else None
    rest_lng = float(restaurant.longitude) if restaurant and getattr(restaurant, "longitude", None) is not None else None

    formatted_restaurant = {
        "id": restaurant.id if restaurant else None,
        "name": restaurant.name if restaurant else "Restaurant",
        "address": restaurant.address if restaurant else None,
        # Return None if phone is genuinely unavailable — never fabricate
        "phone": getattr(restaurant, "phone", None) or None,
        "latitude": rest_lat,
        "longitude": rest_lng
    }

    total_amount = float(order.total_amount) if order and order.total_amount else 0.0
    # Use only real earnings recorded on the assignment; never fabricate
    estimated_earnings = float(getattr(assignment, "earnings", None) or 0.0) or None
    tip_amount = float(getattr(order, "tip_amount", None) or 0.0) or None

    # Real payment values only — no fallback fabrication
    payment_method = (order.payment_method if order and hasattr(order, "payment_method") and order.payment_method else None)
    payment_status = (order.payment_status if order and hasattr(order, "payment_status") and order.payment_status else None)

    return {
        "assignment_id": assignment.id,
        "order_id": assignment.order_id,
        "order_type": order.order_type if order and getattr(order, "order_type", None) else "DELIVERY",
        "display_order_id": f"ORD-{str(assignment.order_id).zfill(6)}",
        "status": assignment.status,
        "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "restaurant_name": formatted_restaurant["name"],
        "restaurant_address": formatted_restaurant["address"],
        "restaurant": formatted_restaurant,
        "customer_address": customer_address_str,
        "delivery_address": formatted_delivery_addr,
        "items": items,
        "items_count": len(items) if items else items_count,
        "total_amount": total_amount,
        "tip_amount": tip_amount,
        "estimated_earnings": estimated_earnings,
        "payment_method": payment_method,
        "payment_status": payment_status,
    }


@router.get("/api/v1/rider/delivery-requests")
def get_delivery_requests(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status == "ASSIGNED"
    ).all()

    requests = []
    for assignment in assignments:
        order = db.query(Order).filter(
            Order.id == assignment.order_id,
            Order.order_type.ilike("DELIVERY")
        ).first()
        if order:
            restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
            requests.append(format_assignment_data(assignment, order, restaurant, db))
    return {"delivery_requests": requests}


@router.get("/api/v1/rider/deliveries/current")
def get_current_delivery(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status.notin_(["DELIVERED", "REJECTED", "CANCELLED", "FAILED", "ASSIGNED"])
    ).first()

    if not assignment:
        return {"current_assignment": None}

    order = db.query(Order).filter(
        Order.id == assignment.order_id,
        Order.order_type.ilike("DELIVERY")
    ).first()
    if not order:
        return {"current_assignment": None}

    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first() if order else None

    return {
        "current_assignment": format_assignment_data(assignment, order, restaurant, db)
    }


@router.get("/api/v1/rider/available-orders")
def get_available_orders(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    """
    Returns all unassigned delivery orders so any on-duty rider can see and accept them.
    Only returns orders of type DELIVERY that have an UNASSIGNED assignment record.
    """
    unassigned_assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.status == "UNASSIGNED",
        DeliveryAssignment.rider_id == None
    ).all()

    result = []
    for assignment in unassigned_assignments:
        order = db.query(Order).filter(
            Order.id == assignment.order_id,
            Order.order_type.ilike("DELIVERY"),
            Order.status.notin_(["CANCELLED", "COMPLETED"])
        ).first()
        if order:
            restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
            result.append(format_assignment_data(assignment, order, restaurant, db))

    return {"delivery_requests": result}


@router.post("/api/v1/rider/available-orders/{order_id}/accept")
def accept_available_order(order_id: int, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    """
    Allows an on-duty rider to accept an unassigned delivery order.
    Updates the assignment with the rider's ID and transitions status to ASSIGNED.
    """
    from ..models.delivery import DeliveryStatusHistory
    from ..services.delivery_status import update_delivery_status

    rider_id = current_rider.id

    # Ensure the rider has no other active delivery
    active = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status.notin_(["DELIVERED", "REJECTED", "CANCELLED", "FAILED", "UNASSIGNED"])
    ).first()
    if active:
        raise HTTPException(status_code=400, detail="You already have an active delivery. Complete it before accepting a new one.")

    # Find the UNASSIGNED assignment for this order
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.order_id == order_id,
        DeliveryAssignment.status == "UNASSIGNED",
        DeliveryAssignment.rider_id == None
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="This order is no longer available (already accepted or not found).")

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    # Assign to this rider
    assignment.rider_id = rider_id
    assignment.status = "ASSIGNED"
    assignment.assigned_at = datetime.utcnow()
    order.delivery_status = "RIDER_ASSIGNED"

    db.commit()
    db.refresh(assignment)

    # Record status history
    history = DeliveryStatusHistory(
        order_id=order_id,
        delivery_assignment_id=assignment.id,
        rider_id=rider_id,
        status="RIDER_ASSIGNED",
        notes=f"Rider {rider_id} accepted the delivery order"
    )
    db.add(history)
    db.commit()

    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first()
    return {
        "success": True,
        "assignment": format_assignment_data(assignment, order, restaurant, db)
    }


@router.get("/api/v1/rider/stats")
def get_rider_stats(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    total_rides = current_rider.total_rides or 0
    rating = round(current_rider.rating or 5.0, 1)

    today_start = datetime.combine(date.today(), datetime.min.time())
    week_start = today_start - timedelta(days=today_start.weekday())

    today_assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status == "DELIVERED",
        DeliveryAssignment.delivered_at >= today_start
    ).all()

    week_assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status == "DELIVERED",
        DeliveryAssignment.delivered_at >= week_start
    ).all()

    today_earnings = 0.0
    for a in today_assignments:
        today_earnings += float(getattr(a, "earnings", 0.0) or 0.0)

    week_earnings = 0.0
    for a in week_assignments:
        week_earnings += float(getattr(a, "earnings", 0.0) or 0.0)

    return {
        "today_earnings": round(today_earnings, 2),
        "deliveries_today": len(today_assignments),
        "total_rides": total_rides,
        "rating": rating,
        "this_week_earnings": round(week_earnings, 2),
    }


@router.get("/api/v1/rider/deliveries/history")
def get_delivery_history(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id
    ).order_by(DeliveryAssignment.assigned_at.desc()).all()

    history = []
    import json as _json
    for assignment in assignments:
        order = db.query(Order).filter(Order.id == assignment.order_id).first()
        restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first() if order else None

        earnings = float(getattr(assignment, "earnings", None) or 0.0) or None

        # Parse customer name from delivery address snapshot
        customer_name = None
        customer_address = None
        if order and order.delivery_address_snapshot:
            raw = order.delivery_address_snapshot
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except Exception:
                    raw = {}
            if isinstance(raw, dict):
                customer_name = raw.get("contact_name") or raw.get("name") or getattr(order, "customer_name", None)
                customer_address = raw.get("address_line") or raw.get("full_address") or raw.get("address")

        items_count = db.query(OrderItem).filter(OrderItem.order_id == assignment.order_id).count() if order else 0

        history.append({
            "id": assignment.id,
            "order_id": assignment.order_id,
            "display_order_id": f"ORD-{str(assignment.order_id).zfill(6)}",
            "status": assignment.status,
            "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
            "delivered_at": assignment.delivered_at.isoformat() if assignment.delivered_at else None,
            "restaurant_name": restaurant.name if restaurant else None,
            "customer_name": customer_name,
            "customer_address": customer_address,
            "items_count": items_count,
            "earnings": earnings,
            "total_amount": float(order.total_amount) if order and order.total_amount else None,
        })

    return history


@router.get("/api/v1/rider/earnings/history")
def get_earnings_history(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignments = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.rider_id == rider_id,
        DeliveryAssignment.status == "DELIVERED"
    ).order_by(DeliveryAssignment.delivered_at.desc()).all()

    earnings_by_date = defaultdict(lambda: {"total_earnings": 0.0, "deliveries_count": 0})

    for assignment in assignments:
        date_str = (assignment.delivered_at or assignment.assigned_at or datetime.utcnow()).strftime("%Y-%m-%d")
        earnings = float(getattr(assignment, "earnings", 0.0) or 0.0)
        
        earnings_by_date[date_str]["total_earnings"] += earnings
        earnings_by_date[date_str]["deliveries_count"] += 1

    earnings_list = []
    for date_key, data in sorted(earnings_by_date.items(), reverse=True):
        earnings_list.append({
            "date": date_key,
            "total_earnings": round(data["total_earnings"], 2),
            "deliveries_count": data["deliveries_count"]
        })

    return earnings_list


@router.get("/api/v1/rider/deliveries/{assignment_id}")
def get_rider_delivery_assignment(assignment_id: int, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider_id = current_rider.id
    assignment = db.query(DeliveryAssignment).filter(
        DeliveryAssignment.id == assignment_id,
        DeliveryAssignment.rider_id == rider_id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Delivery assignment not found")

    order = db.query(Order).filter(Order.id == assignment.order_id).first()
    restaurant = db.query(Restaurant).filter(Restaurant.id == order.restaurant_id).first() if order else None

    return format_assignment_data(assignment, order, restaurant, db)


# ── Delivery Partner profile endpoints (called by frontend) ──────────────────

@router.get("/api/v1/delivery-partners/{rider_id}")
def get_delivery_partner(rider_id: int, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    return {
        "id": rider.id,
        "name": rider.name,
        "phone": rider.phone,
        "email": rider.email,
        "vehicle_type": rider.vehicle_type,
        "vehicle_number": rider.vehicle_number,
        "rating": round(rider.rating or 0.0, 1),
        "total_rides": rider.total_rides or 0,
        "is_online": rider.is_online,
        "is_available": rider.is_available,
        "profile_image": rider.profile_image,
    }


class OnlineStatusPayload(BaseModel):
    is_online: bool


class AvailabilityPayload(BaseModel):
    is_available: bool


@router.patch("/api/v1/delivery-partners/{rider_id}/online-status")
def patch_online_status(rider_id: int, payload: OnlineStatusPayload, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    rider.is_online = payload.is_online
    if not payload.is_online:
        rider.is_available = False
    db.commit()
    return {"id": rider.id, "is_online": rider.is_online, "is_available": rider.is_available}


@router.patch("/api/v1/delivery-partners/{rider_id}/availability")
def patch_availability(rider_id: int, payload: AvailabilityPayload, db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    rider.is_available = payload.is_available
    db.commit()
    return {"id": rider.id, "is_online": rider.is_online, "is_available": rider.is_available}


# ── Authenticated Rider Endpoints (/me) ───────────────────────────────────

@router.get("/api/v1/riders/me")
def get_rider_me(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == current_rider.id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    
    return {
        "id": rider.id,
        "name": rider.name,
        "phone": rider.phone,
        "email": rider.email,
        "profile_image": rider.profile_image,
        "vehicle_type": rider.vehicle_type,
        "vehicle_number": rider.vehicle_number,
        "rating": round(rider.rating or 0.0, 1),
        "total_ratings": rider.total_ratings or 0,
        "total_rides": rider.total_rides or 0,
        "is_online": rider.is_online,
        "is_available": rider.is_available,
        "is_active": rider.is_active,
        "current_latitude": rider.current_latitude,
        "current_longitude": rider.current_longitude,
        "last_location_at": rider.last_location_at,
    }

class RiderUpdatePayload(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    profile_image: Optional[str] = None

@router.patch("/api/v1/riders/me")
def update_rider_me(payload: RiderUpdatePayload, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == current_rider.id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    
    if payload.name is not None:
        rider.name = payload.name
    if payload.email is not None:
        rider.email = payload.email
    if payload.profile_image is not None:
        rider.profile_image = payload.profile_image
        
    db.commit()
    db.refresh(rider)
    return get_rider_me(current_rider, db)


@router.get("/api/v1/riders/me/vehicle")
def get_rider_vehicle(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == current_rider.id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    return {
        "vehicle_type": rider.vehicle_type,
        "vehicle_number": rider.vehicle_number,
    }

class VehicleUpdatePayload(BaseModel):
    vehicle_type: Optional[str] = None
    vehicle_number: Optional[str] = None

@router.patch("/api/v1/riders/me/vehicle")
def update_rider_vehicle(payload: VehicleUpdatePayload, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == current_rider.id).first()
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")
    
    if payload.vehicle_type is not None:
        rider.vehicle_type = payload.vehicle_type
    if payload.vehicle_number is not None:
        rider.vehicle_number = payload.vehicle_number
        
    db.commit()
    return {
        "vehicle_type": rider.vehicle_type,
        "vehicle_number": rider.vehicle_number,
    }

class RiderDocumentPayload(BaseModel):
    aadhaar_front: Optional[str] = None
    aadhaar_back: Optional[str] = None
    pan_card: Optional[str] = None
    license: Optional[str] = None

@router.get("/api/v1/riders/me/documents")
def get_rider_documents(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    docs = db.query(RiderDocument).filter(RiderDocument.rider_id == current_rider.id).all()
    result = {
        "aadhaar_front": None,
        "aadhaar_back": None,
        "pan_card": None,
        "license": None,
        "is_verified": False
    }
    
    verified_count = 0
    for doc in docs:
        if doc.document_type == "aadhaar_front":
            result["aadhaar_front"] = doc.document_url
        elif doc.document_type == "aadhaar_back":
            result["aadhaar_back"] = doc.document_url
        elif doc.document_type == "pan_card":
            result["pan_card"] = doc.document_url
        elif doc.document_type == "license":
            result["license"] = doc.document_url
            
        if doc.verification_status == "VERIFIED":
            verified_count += 1
            
    # Assuming verified if all 4 docs are verified
    if verified_count >= 4:
        result["is_verified"] = True
        
    return result

@router.patch("/api/v1/riders/me/documents")
def update_rider_documents(payload: RiderDocumentPayload, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    docs = db.query(RiderDocument).filter(RiderDocument.rider_id == current_rider.id).all()
    doc_map = {doc.document_type: doc for doc in docs}
    
    def update_or_create(doc_type, url):
        if doc_type in doc_map:
            doc_map[doc_type].document_url = url
            doc_map[doc_type].verification_status = "PENDING"
        else:
            new_doc = RiderDocument(
                rider_id=current_rider.id,
                document_type=doc_type,
                document_url=url,
                verification_status="PENDING"
            )
            db.add(new_doc)
            
    if payload.aadhaar_front is not None:
        update_or_create("aadhaar_front", payload.aadhaar_front)
    if payload.aadhaar_back is not None:
        update_or_create("aadhaar_back", payload.aadhaar_back)
    if payload.pan_card is not None:
        update_or_create("pan_card", payload.pan_card)
    if payload.license is not None:
        update_or_create("license", payload.license)
        
    db.commit()
    
    return get_rider_documents(current_rider, db)

class RiderBankDetailPayload(BaseModel):
    account_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None

@router.get("/api/v1/riders/me/bank-details")
def get_rider_bank_details(current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    bank = db.query(RiderBankDetail).filter(RiderBankDetail.rider_id == current_rider.id).first()
    if not bank:
        return {}
    return {
        "account_name": bank.account_name,
        "account_number": bank.account_number,
        "ifsc_code": bank.ifsc_code,
        "bank_name": bank.bank_name
    }

@router.patch("/api/v1/riders/me/bank-details")
def update_rider_bank_details(payload: RiderBankDetailPayload, current_rider=Depends(get_current_rider), db: Session = Depends(get_db)):
    bank = db.query(RiderBankDetail).filter(RiderBankDetail.rider_id == current_rider.id).first()
    if not bank:
        bank = RiderBankDetail(rider_id=current_rider.id)
        db.add(bank)
        
    if payload.account_name is not None:
        bank.account_name = payload.account_name
    if payload.account_number is not None:
        bank.account_number = payload.account_number
    if payload.ifsc_code is not None:
        bank.ifsc_code = payload.ifsc_code
    if payload.bank_name is not None:
        bank.bank_name = payload.bank_name
        
    db.commit()
    db.refresh(bank)
    return {
        "account_name": bank.account_name,
        "account_number": bank.account_number,
        "ifsc_code": bank.ifsc_code,
        "bank_name": bank.bank_name
    }


from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from ..db import SessionLocal
from ..models.customer import Customer, CustomerAddress
from ..utils.dependencies import get_current_customer, get_db
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Customer Delivery"])


class AddressCreatePayload(BaseModel):
    address_type: str = "Home"
    flat_house_no: str
    floor: Optional[str] = None
    building_apartment_name: Optional[str] = None
    landmark: Optional[str] = None
    full_address: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: str = "Chennai"
    state: str = "Tamil Nadu"
    pincode: str
    contact_name: str
    contact_phone: str
    delivery_instructions: Optional[str] = None
    is_default: bool = False

class AddressUpdatePayload(BaseModel):
    address_type: Optional[str] = None
    flat_house_no: Optional[str] = None
    floor: Optional[str] = None
    building_apartment_name: Optional[str] = None
    landmark: Optional[str] = None
    full_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    delivery_instructions: Optional[str] = None
    is_default: Optional[bool] = None

@router.get("/api/v1/public/customers/{customer_id}/addresses")
def get_customer_addresses(
    customer_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to access these addresses")
    addresses = db.query(CustomerAddress).filter(
        CustomerAddress.customer_id == customer_id,
        CustomerAddress.is_active == True
    ).all()
    return addresses

@router.post("/api/v1/public/customers/{customer_id}/addresses")
def create_customer_address(
    customer_id: int, 
    payload: AddressCreatePayload, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to create address for this customer")
    customer = current_customer

    # If database has a customer_profiles table referenced by foreign key, ensure entry exists
    try:
        from sqlalchemy import text
        from datetime import datetime
        db.execute(text("""
            INSERT INTO customer_profiles (id, phone, name, email, created_at)
            VALUES (:id, :phone, :name, :email, :created_at)
            ON CONFLICT (id) DO NOTHING
        """), {
            "id": customer.id,
            "phone": customer.phone,
            "name": customer.name or "Customer",
            "email": getattr(customer, "email", None),
            "created_at": getattr(customer, "created_at", datetime.utcnow())
        })
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to synchronize customer profile for customer_id=%s", customer_id)
        raise HTTPException(status_code=500, detail="Customer profile could not be prepared for this address")
        
    if payload.is_default:
        db.query(CustomerAddress).filter(
            CustomerAddress.customer_id == customer.id,
            CustomerAddress.is_default == True
        ).update({"is_default": False})
        
    new_address = CustomerAddress(
        customer_id=customer.id,
        address_type=payload.address_type,
        flat_house_no=payload.flat_house_no,
        floor=payload.floor,
        building_apartment_name=payload.building_apartment_name,
        landmark=payload.landmark,
        full_address=payload.full_address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        city=payload.city,
        state=payload.state,
        pincode=payload.pincode,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        delivery_instructions=payload.delivery_instructions,
        is_default=payload.is_default,
        is_active=True
    )
    db.add(new_address)
    try:
        db.commit()
        db.refresh(new_address)
    except Exception:
        db.rollback()
        logger.exception("Failed to save customer address for customer_id=%s", customer_id)
        raise HTTPException(status_code=500, detail="Address could not be saved. Please try again.")
    return new_address

@router.patch("/api/v1/public/customers/{customer_id}/addresses/{address_id}")
def update_customer_address(
    customer_id: int, 
    address_id: int, 
    payload: AddressUpdatePayload, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to update this address")
    address = db.query(CustomerAddress).filter(
        CustomerAddress.id == address_id,
        CustomerAddress.customer_id == customer_id,
        CustomerAddress.is_active == True
    ).first()
    
    if not address:
        raise HTTPException(status_code=404, detail="Address not found or does not belong to customer")
        
    if payload.is_default:
        db.query(CustomerAddress).filter(
            CustomerAddress.customer_id == customer_id,
            CustomerAddress.is_default == True,
            CustomerAddress.id != address_id
        ).update({"is_default": False})
        
    update_data = payload.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(address, key, value)
        
    db.commit()
    db.refresh(address)
    return address

@router.delete("/api/v1/public/customers/{customer_id}/addresses/{address_id}")
def delete_customer_address(
    customer_id: int, 
    address_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this address")
    address = db.query(CustomerAddress).filter(
        CustomerAddress.id == address_id,
        CustomerAddress.customer_id == customer_id,
        CustomerAddress.is_active == True
    ).first()
    
    if not address:
        raise HTTPException(status_code=404, detail="Address not found or does not belong to customer")
        
    address.is_active = False
    db.commit()
    return {"success": True}

# ──────────────────────────────────────────────────────────────────────────────
# Loyalty Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/v1/public/customers/{customer_id}/loyalty")
def get_customer_loyalty(
    customer_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Fetch current loyalty balance and recent transactions."""
    from ..models.customer import LoyaltyTransaction
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this loyalty data")
    customer = current_customer
        
    transactions = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.customer_id == customer_id
    ).order_by(LoyaltyTransaction.created_at.desc()).limit(20).all()
    
    return {
        "balance": getattr(customer, "loyalty_points", 0),
        "transactions": [
            {
                "id": t.id,
                "order_id": t.order_id,
                "points": t.points,
                "type": t.transaction_type,
                "description": t.description,
                "date": t.created_at
            }
            for t in transactions
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# Favorites Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/v1/public/customers/{customer_id}/favorites")
def get_customer_favorites(
    customer_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Fetch all favorited menu items for a customer."""
    from ..models.customer import CustomerFavorite
    from ..models.restaurant import MenuItem
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to access these favorites")
        
    favorites = db.query(CustomerFavorite).filter(CustomerFavorite.customer_id == customer_id).all()
    
    result = []
    for fav in favorites:
        item = db.query(MenuItem).filter(MenuItem.id == fav.menu_item_id).first()
        if item:
            result.append({
                "favorite_id": fav.id,
                "menu_item_id": item.id,
                "name": item.name,
                "price": item.price,
                "image_url": getattr(item, "image_url", None),
                "is_veg": getattr(item, "is_veg", True)
            })
            
    return result

@router.post("/api/v1/public/customers/{customer_id}/favorites/{menu_item_id}")
def add_customer_favorite(
    customer_id: int, 
    menu_item_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Add a menu item to customer favorites."""
    from ..models.customer import CustomerFavorite
    from ..models.restaurant import MenuItem
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to add favorites for this customer")
        
    item = db.query(MenuItem).filter(MenuItem.id == menu_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
        
    existing = db.query(CustomerFavorite).filter(
        CustomerFavorite.customer_id == customer_id,
        CustomerFavorite.menu_item_id == menu_item_id
    ).first()
    
    if not existing:
        fav = CustomerFavorite(customer_id=customer_id, menu_item_id=menu_item_id)
        db.add(fav)
        db.commit()
        
    return {"success": True, "message": "Added to favorites"}

@router.delete("/api/v1/public/customers/{customer_id}/favorites/{menu_item_id}")
def remove_customer_favorite(
    customer_id: int, 
    menu_item_id: int, 
    current_customer = Depends(get_current_customer),
    db: Session = Depends(get_db)
):
    """Remove a menu item from customer favorites."""
    from ..models.customer import CustomerFavorite
    if customer_id != current_customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to remove favorites for this customer")
    
    fav = db.query(CustomerFavorite).filter(
        CustomerFavorite.customer_id == customer_id,
        CustomerFavorite.menu_item_id == menu_item_id
    ).first()
    
    if fav:
        db.delete(fav)
        db.commit()
        
    return {"success": True, "message": "Removed from favorites"}

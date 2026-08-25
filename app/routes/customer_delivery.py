from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from ..db import SessionLocal
from ..models.customer import Customer, CustomerAddress

router = APIRouter(tags=["Customer Delivery"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
def get_customer_addresses(customer_id: int, db: Session = Depends(get_db)):
    addresses = db.query(CustomerAddress).filter(
        CustomerAddress.customer_id == customer_id,
        CustomerAddress.is_active == True
    ).all()
    return addresses

@router.post("/api/v1/public/customers/{customer_id}/addresses")
def create_customer_address(customer_id: int, payload: AddressCreatePayload, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
        
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
    db.commit()
    db.refresh(new_address)
    return new_address

@router.patch("/api/v1/public/customers/{customer_id}/addresses/{address_id}")
def update_customer_address(customer_id: int, address_id: int, payload: AddressUpdatePayload, db: Session = Depends(get_db)):
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
def delete_customer_address(customer_id: int, address_id: int, db: Session = Depends(get_db)):
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

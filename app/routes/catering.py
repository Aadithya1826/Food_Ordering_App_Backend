# pyrefly: ignore [missing-import]
# Trigger reload
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
import os
try:
    # pyrefly: ignore [missing-import]
    import razorpay
except ImportError:
    razorpay = None

from ..db import SessionLocal
from ..utils.dependencies import get_db, get_current_customer
from ..models.customer import Customer
from ..models.menu import CateringOrderMenu
from ..models.catering import (
    CateringSession, CateringSessionCustomization, CateringSessionAddon,
    CateringOrder, CateringOrderCustomization, CateringOrderAddon, CateringPayment
)
from ..schemas.catering import (
    CateringPackageResponse, CateringPackageWithItemsResponse, CateringCategoryResponse,
    CateringPackageItemResponse, CateringSwapOptionResponse, CateringSwapOptionsListResponse,
    CateringSessionCreate, CateringSessionEventUpdate, CateringSessionResponse,
    CateringCustomizationCreate, CateringCustomizationResponse, CateringAddonCreate,
    CateringAddonResponse, CateringQuoteResponse, CateringPaymentRequest,
    CateringPaymentVerifyRequest, CateringOrderResponse, CateringOrderListResponse,
    CateringBalancePaymentRequest, CateringBalancePaymentVerifyRequest
)
from ..services.catering_service import recalculate_catering_session

router = APIRouter(
    prefix="/api/v1/public",
    tags=["Catering"]
)

@router.get("/catering/packages", response_model=List[CateringPackageResponse])
def get_available_packages(restaurant_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(
        CateringOrderMenu.name,
        CateringOrderMenu.code,
        CateringOrderMenu.price,
        CateringOrderMenu.minimum_order_quantity,
        CateringOrderMenu.is_available
    ).filter(CateringOrderMenu.is_available == True)
    
    if restaurant_id is not None:
        query = query.filter((CateringOrderMenu.restaurant_id == restaurant_id) | (CateringOrderMenu.restaurant_id == None))
        
    # Group packages by uniqueness
    results = query.group_by(
        CateringOrderMenu.name,
        CateringOrderMenu.code,
        CateringOrderMenu.price,
        CateringOrderMenu.minimum_order_quantity,
        CateringOrderMenu.is_available
    ).all()
    
    packages_data = []
    for row in results:
        # Fetch items for this package
        items = db.query(CateringOrderMenu.item_name).filter(CateringOrderMenu.code == row.code).order_by(CateringOrderMenu.display_order).all()
        
        item_names = []
        for i in items:
            if i.item_name and i.item_name not in item_names:
                item_names.append(i.item_name)
        
        summary = None
        if len(item_names) > 0:
            if len(item_names) <= 5:
                summary = ", ".join(item_names[:-1]) + (" & " + item_names[-1] if len(item_names) > 1 else item_names[0])
            else:
                summary = ", ".join(item_names[:4]) + f" & {len(item_names) - 4} more"

        packages_data.append(
            CateringPackageResponse(
                name=row.name,
                code=row.code,
                price=row.price,
                minimum_order_quantity=row.minimum_order_quantity,
                is_available=row.is_available,
                items_summary=summary
            )
        )
        
    return packages_data

@router.get("/catering/packages/{package_code}/items", response_model=CateringPackageWithItemsResponse)
def get_package_items(package_code: str, db: Session = Depends(get_db)):
    items = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == package_code).order_by(CateringOrderMenu.display_order).all()
    if not items:
        raise HTTPException(status_code=404, detail="Package not found")
        
    pkg = items[0]
    
    # Group by category_name
    from collections import defaultdict
    categories = defaultdict(list)
    for item in items:
        if item.category_name:
            categories[item.category_name].append(
                CateringPackageItemResponse(
                    id=item.id,
                    name=item.item_name,
                    customization_group=item.customization_group,
                    is_swappable=item.is_swappable,
                    is_removable=item.is_removable
                )
            )
            
    return CateringPackageWithItemsResponse(
        package=CateringPackageResponse(
            name=pkg.name,
            code=pkg.code,
            price=pkg.price,
            minimum_order_quantity=pkg.minimum_order_quantity,
            is_available=pkg.is_available
        ),
        categories=[
            CateringCategoryResponse(name=k, items=v)
            for k, v in categories.items()
        ]
    )

@router.get("/catering/items/{item_id}/swap-options", response_model=CateringSwapOptionsListResponse)
def get_swap_options(item_id: int, db: Session = Depends(get_db)):
    original = db.query(CateringOrderMenu).filter(CateringOrderMenu.id == item_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Item not found")
        
    options = []
    
    # Same customization group (Normally replace price 0)
    same_group_items = db.query(CateringOrderMenu).filter(
        CateringOrderMenu.customization_group == original.customization_group,
        CateringOrderMenu.id != original.id,
        CateringOrderMenu.is_available == True
    ).all()
    
    for opt in same_group_items:
        options.append(CateringSwapOptionResponse(
            id=opt.id,
            name=opt.item_name,
            group=opt.customization_group,
            price_change_per_person=original.same_group_replace_price
        ))
        
    # Upgrade group
    if original.upgrade_group:
        upgrade_items = db.query(CateringOrderMenu).filter(
            CateringOrderMenu.customization_group == original.upgrade_group,
            CateringOrderMenu.id != original.id,
            CateringOrderMenu.is_available == True
        ).all()
        for opt in upgrade_items:
            options.append(CateringSwapOptionResponse(
                id=opt.id,
                name=opt.item_name,
                group=opt.customization_group,
                price_change_per_person=original.upgrade_price
            ))
            
    # Remove duplicates if any (grouped by id)
    unique_opts = {o.id: o for o in options}
    
    return CateringSwapOptionsListResponse(
        original_item={
            "id": original.id,
            "name": original.item_name,
            "group": original.customization_group
        },
        options=list(unique_opts.values())
    )

@router.get("/catering/items/add-options")
def get_add_options(db: Session = Depends(get_db)):
    items = db.query(CateringOrderMenu).filter(CateringOrderMenu.is_available == True).all()
    
    seen = set()
    unique_items = []
    for item in items:
        if item.item_name not in seen:
            seen.add(item.item_name)
            unique_items.append({
                "id": item.id,
                "name": item.item_name,
                "group": item.customization_group,
                "price_change_per_person": item.add_price
            })
            
    return {"options": unique_items}

@router.post("/catering/sessions", response_model=CateringSessionResponse)
def create_catering_session(
    data: CateringSessionCreate, 
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    if data.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Not authorized to create session for this customer")
        
    pkg = db.query(CateringOrderMenu).filter(
        CateringOrderMenu.code == data.package_code,
        CateringOrderMenu.is_available == True
    ).first()
    
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found or unavailable")
        
    if data.guest_count < pkg.minimum_order_quantity:
        raise HTTPException(status_code=400, detail=f"Guest count must be at least {pkg.minimum_order_quantity}")
        
    session = CateringSession(
        customer_id=customer.id,
        restaurant_id=data.restaurant_id,
        package_name=pkg.name,
        package_code=pkg.code,
        package_price=pkg.price,
        guest_count=data.guest_count,
        status="DRAFT"
    )
    
    db.add(session)
    db.commit()
    
    recalculate_catering_session(db, session)
    return session

@router.patch("/catering/sessions/{session_id}/event", response_model=CateringSessionResponse)
def update_event_details(
    session_id: str,
    data: CateringSessionEventUpdate,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    if data.event_name is not None:
        session.event_name = data.event_name
    if data.event_date is not None:
        try:
            session.event_date = datetime.strptime(data.event_date, "%Y-%m-%d")
            if session.event_date < datetime.now():
                raise HTTPException(status_code=400, detail="Event date cannot be in the past")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    if data.serving_time is not None:
        session.serving_time = data.serving_time
    if data.occasion is not None:
        session.occasion = data.occasion
    if data.service_type is not None:
        session.service_type = data.service_type
    if data.spice_level is not None:
        session.spice_level = data.spice_level
    if data.dietary_notes is not None:
        session.dietary_notes = data.dietary_notes
        
    if data.delivery_address_id is not None:
        from ..models.customer import CustomerAddress
        addr = db.query(CustomerAddress).filter(CustomerAddress.id == data.delivery_address_id, CustomerAddress.customer_id == customer.id).first()
        if not addr:
            raise HTTPException(status_code=404, detail="Address not found")
        session.delivery_address_id = addr.id
        session.delivery_address_snapshot = addr.full_address
        
    db.commit()
    recalculate_catering_session(db, session)
    return session

@router.post("/catering/sessions/{session_id}/customizations", response_model=CateringCustomizationResponse)
def add_customization(
    session_id: str,
    data: CateringCustomizationCreate,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    customization = CateringSessionCustomization(
        catering_session_id=session.id,
        action_type=data.action_type,
        guest_count=session.guest_count
    )
    
    if data.action_type == "REMOVE":
        orig = db.query(CateringOrderMenu).filter(CateringOrderMenu.id == data.original_item_id, CateringOrderMenu.code == session.package_code).first()
        if not orig or not orig.is_removable:
            raise HTTPException(status_code=400, detail="Invalid or non-removable item")
        customization.original_item_id = orig.id
        customization.original_item_name = orig.item_name
        customization.original_category = orig.category_name
        customization.original_group = orig.customization_group
        customization.price_adjustment_per_person = orig.remove_price
        
    elif data.action_type == "ADD":
        new_item = db.query(CateringOrderMenu).filter(CateringOrderMenu.id == data.new_item_id, CateringOrderMenu.is_available == True).first()
        if not new_item:
            raise HTTPException(status_code=400, detail="Invalid item")
        # Find the add_price from the package (using master for simplicity)
        customization.new_item_id = new_item.id
        customization.new_item_name = new_item.item_name
        customization.new_category = new_item.category_name
        customization.new_group = new_item.customization_group
        customization.price_adjustment_per_person = new_item.add_price
        
    elif data.action_type == "REPLACE":
        orig = db.query(CateringOrderMenu).filter(CateringOrderMenu.id == data.original_item_id, CateringOrderMenu.code == session.package_code).first()
        if not orig or not orig.is_swappable:
            raise HTTPException(status_code=400, detail="Invalid or non-swappable item")
            
        new_item = db.query(CateringOrderMenu).filter(CateringOrderMenu.id == data.new_item_id, CateringOrderMenu.is_available == True).first()
        if not new_item:
            raise HTTPException(status_code=400, detail="Invalid replacement item")
            
        if new_item.customization_group == orig.customization_group:
            customization.price_adjustment_per_person = orig.same_group_replace_price
        elif new_item.customization_group == orig.upgrade_group:
            customization.price_adjustment_per_person = orig.upgrade_price
        else:
            raise HTTPException(status_code=400, detail="Invalid replacement option")
            
        customization.original_item_id = orig.id
        customization.original_item_name = orig.item_name
        customization.original_category = orig.category_name
        customization.original_group = orig.customization_group
        
        customization.new_item_id = new_item.id
        customization.new_item_name = new_item.item_name
        customization.new_category = new_item.category_name
        customization.new_group = new_item.customization_group
    else:
        raise HTTPException(status_code=400, detail="Invalid action_type")
        
    customization.total_price_adjustment = customization.price_adjustment_per_person * session.guest_count
    
    db.add(customization)
    db.commit()
    db.refresh(customization)
    
    recalculate_catering_session(db, session)
    return customization

@router.delete("/catering/sessions/{session_id}/customizations/{customization_id}")
def delete_customization(
    session_id: str,
    customization_id: int,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    cust = db.query(CateringSessionCustomization).filter(CateringSessionCustomization.id == customization_id, CateringSessionCustomization.catering_session_id == session.id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customization not found")
        
    db.delete(cust)
    db.commit()
    recalculate_catering_session(db, session)
    return {"status": "success"}

@router.get("/catering/sessions/{session_id}/customizations", response_model=List[CateringCustomizationResponse])
def list_session_customizations(
    session_id: str,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    return session.customizations

@router.post("/catering/sessions/{session_id}/addons", response_model=CateringAddonResponse)
def add_addon(
    session_id: str,
    data: CateringAddonCreate,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    addon = CateringSessionAddon(
        catering_session_id=session.id,
        addon_type=data.addon_type,
        addon_name=data.addon_name,
        quantity=data.quantity,
        unit_price=data.unit_price,
        pricing_type=data.pricing_type
    )
    
    if data.pricing_type == "PER_PERSON":
        addon.total_price = data.unit_price * session.guest_count
    elif data.pricing_type == "PER_UNIT":
        addon.total_price = data.unit_price * data.quantity
    elif data.pricing_type == "FLAT":
        addon.total_price = data.unit_price
    else:
        raise HTTPException(status_code=400, detail="Invalid pricing_type")
        
    db.add(addon)
    db.commit()
    db.refresh(addon)
    
    recalculate_catering_session(db, session)
    return addon

@router.delete("/catering/sessions/{session_id}/addons/{addon_id}")
def delete_addon(
    session_id: str,
    addon_id: int,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    addon = db.query(CateringSessionAddon).filter(CateringSessionAddon.id == addon_id, CateringSessionAddon.catering_session_id == session.id).first()
    if not addon:
        raise HTTPException(status_code=404, detail="Addon not found")
        
    db.delete(addon)
    db.commit()
    recalculate_catering_session(db, session)
    return {"status": "success"}

@router.get("/catering/sessions/{session_id}", response_model=CateringSessionResponse)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    return session

@router.post("/catering/sessions/{session_id}/quote", response_model=CateringQuoteResponse)
def generate_quote(
    session_id: str,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        raise HTTPException(status_code=400, detail="Cannot modify a converted session")
        
    session.status = "QUOTED"
    db.commit()
    recalculate_catering_session(db, session)
    
    return CateringQuoteResponse(
        session_id=session.id,
        status=session.status,
        package_name=session.package_name,
        package_price=session.package_price,
        guest_count=session.guest_count,
        base_amount=session.base_amount,
        customization_amount=session.customization_amount,
        addon_amount=session.addon_amount,
        transport_charge=session.transport_charge,
        service_charge=session.service_charge,
        cgst_amount=session.cgst_amount,
        sgst_amount=session.sgst_amount,
        total_amount=session.total_amount,
        advance_percentage=session.advance_percentage,
        advance_amount=session.advance_amount,
        balance_amount=session.balance_amount
    )

@router.post("/catering/sessions/{session_id}/payment")
def start_payment(
    session_id: str,
    data: CateringPaymentRequest,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if not session.event_date or not session.serving_time:
        raise HTTPException(status_code=400, detail="Missing required event details")
        
    recalculate_catering_session(db, session)
    
    min_advance = session.total_amount * 0.50
    if data.payment_amount < min_advance:
        raise HTTPException(status_code=400, detail="Minimum advance payment is 50% of the order total.")
    if data.payment_amount > session.total_amount:
        raise HTTPException(status_code=400, detail="Payment amount cannot exceed the remaining order amount.")

    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not razorpay or not key_id or not key_secret:
        raise HTTPException(status_code=503, detail="Razorpay integration is unavailable on this server.")
    
    client = razorpay.Client(auth=(key_id, key_secret))
    
    razorpay_order = client.order.create({
        "amount": int(data.payment_amount * 100),
        "currency": "INR",
        "receipt": f"cat_sess_{session.id}"
    })
    
    session.status = "PAYMENT_PENDING"
    db.commit()
    
    return {
        "status": "PAYMENT_PENDING",
        "payable_amount": data.payment_amount,
        "razorpay_order_id": razorpay_order["id"]
    }

@router.post("/catering/sessions/{session_id}/payment/verify")
def verify_payment_and_create_order(
    session_id: str,
    data: CateringPaymentVerifyRequest,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    session = db.query(CateringSession).with_for_update().filter(CateringSession.id == session_id).first()
    if not session or session.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if session.status == "CONVERTED":
        order = db.query(CateringOrder).filter(CateringOrder.catering_session_id == session.id).first()
        if order:
            return {
                "success": True,
                "order_id": order.id,
                "payment_status": order.payment_status,
                "order_status": order.order_status,
                "message": "Order already created"
            }
            
    existing_payment = db.query(CateringPayment).filter(CateringPayment.transaction_id == data.razorpay_payment_id).first()
    if existing_payment:
        raise HTTPException(status_code=400, detail="Payment transaction already processed")

    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    client = razorpay.Client(auth=(key_id, key_secret))
    
    if data.payment_method == "CASH":
        pass # Bypass signature verification for testing
    else:
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': data.razorpay_order_id,
                'razorpay_payment_id': data.razorpay_payment_id,
                'razorpay_signature': data.razorpay_signature
            })
        except Exception as e:
            raise HTTPException(status_code=400, detail="Razorpay signature verification failed")

    recalculate_catering_session(db, session)
    
    paid_amount = data.amount
    min_advance = session.total_amount * 0.50
    if paid_amount < min_advance:
        raise HTTPException(status_code=400, detail="Payment amount below minimum advance.")

    full_payment_due = session.event_date - timedelta(days=2) if session.event_date else None
    balance_amount = session.total_amount - paid_amount
    payment_status = "PAID" if balance_amount <= 0 else "PARTIALLY_PAID"

    order = CateringOrder(
        catering_session_id=session.id,
        customer_id=customer.id,
        restaurant_id=session.restaurant_id,
        package_name=session.package_name,
        package_code=session.package_code,
        package_price=session.package_price,
        event_name=session.event_name,
        event_date=session.event_date,
        serving_time=session.serving_time,
        occasion=session.occasion,
        guest_count=session.guest_count,
        service_type=session.service_type,
        delivery_address_id=session.delivery_address_id,
        delivery_address_snapshot=session.delivery_address_snapshot,
        spice_level=session.spice_level,
        dietary_notes=session.dietary_notes,
        base_amount=session.base_amount,
        customization_amount=session.customization_amount,
        addon_amount=session.addon_amount,
        transport_charge=session.transport_charge,
        service_charge=session.service_charge,
        cgst_amount=session.cgst_amount,
        sgst_amount=session.sgst_amount,
        total_amount=session.total_amount,
        advance_percentage=session.advance_percentage,
        advance_amount=session.advance_amount,
        order_status="CONFIRMED",
        paid_amount=paid_amount,
        balance_amount=balance_amount,
        payment_status=payment_status,
        full_payment_due_date=full_payment_due
    )
    
    db.add(order)
    db.flush()
    
    payment = CateringPayment(
        customer_id=session.customer_id,
        catering_order_id=order.id,
        transaction_id=data.razorpay_payment_id,
        amount=paid_amount,
        payment_type="ADVANCE",
        payment_method=data.payment_method or "ONLINE",
        payment_status="SUCCESS"
    )
    db.add(payment)
    
    for cust in session.customizations:
        order_cust = CateringOrderCustomization(
            catering_order_id=order.id,
            action_type=cust.action_type,
            original_item_name=cust.original_item_name,
            original_category=cust.original_category,
            original_group=cust.original_group,
            new_item_name=cust.new_item_name,
            new_category=cust.new_category,
            new_group=cust.new_group,
            price_adjustment_per_person=cust.price_adjustment_per_person,
            guest_count=cust.guest_count,
            total_price_adjustment=cust.total_price_adjustment
        )
        db.add(order_cust)
        
    # Copy Addons
    for addon in session.addons:
        order_addon = CateringOrderAddon(
            catering_order_id=order.id,
            addon_type=addon.addon_type,
            addon_name=addon.addon_name,
            quantity=addon.quantity,
            unit_price=addon.unit_price,
            pricing_type=addon.pricing_type,
            total_price=addon.total_price
        )
        db.add(order_addon)
        
    session.status = "CONVERTED"
    db.commit()
    
    return {
        "success": True,
        "order_id": order.id,
        "payment_status": order.payment_status,
        "order_status": order.order_status
    }

@router.get("/catering/orders/{order_id}", response_model=CateringOrderResponse)
def get_final_order(
    order_id: int,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    order = db.query(CateringOrder).filter(CateringOrder.id == order_id).first()
    if not order or order.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Order not found")
        
    return order

@router.get("/customers/{customer_id}/catering-orders", response_model=CateringOrderListResponse)
def get_customer_catering_orders(
    customer_id: int,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    if customer.id != customer_id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    orders = db.query(CateringOrder).options(joinedload(CateringOrder.payments)).filter(CateringOrder.customer_id == customer_id).order_by(CateringOrder.created_at.desc()).all()
    return CateringOrderListResponse(data=orders)

@router.post("/catering/orders/{order_id}/balance-payment")
def start_balance_payment(
    order_id: int,
    data: CateringBalancePaymentRequest,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    order = db.query(CateringOrder).filter(CateringOrder.id == order_id).first()
    if not order or order.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Order not found")
        
    if order.balance_amount <= 0:
        raise HTTPException(status_code=400, detail="No balance amount pending.")
        
    if data.payment_amount > order.balance_amount:
        raise HTTPException(status_code=400, detail="Payment amount cannot exceed the remaining balance.")
        
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not razorpay or not key_id or not key_secret:
        raise HTTPException(status_code=503, detail="Razorpay integration is unavailable on this server.")
    
    client = razorpay.Client(auth=(key_id, key_secret))
    
    razorpay_order = client.order.create({
        "amount": int(data.payment_amount * 100),
        "currency": "INR",
        "receipt": f"cat_ord_{order.id}"
    })
    
    return {
        "status": "PAYMENT_PENDING",
        "payable_amount": data.payment_amount,
        "razorpay_order_id": razorpay_order["id"]
    }

@router.post("/catering/orders/{order_id}/balance-payment/verify")
def verify_balance_payment(
    order_id: int,
    data: CateringBalancePaymentVerifyRequest,
    db: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer)
):
    order = db.query(CateringOrder).with_for_update().filter(CateringOrder.id == order_id).first()
    if not order or order.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="Order not found")
        
    if order.balance_amount <= 0:
        raise HTTPException(status_code=400, detail="Order is already fully paid.")
        
    existing_payment = db.query(CateringPayment).filter(CateringPayment.transaction_id == data.razorpay_payment_id).first()
    if existing_payment:
        raise HTTPException(status_code=400, detail="Payment transaction already processed")

    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    client = razorpay.Client(auth=(key_id, key_secret))
    
    if data.payment_method == "CASH":
        pass
    else:
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': data.razorpay_order_id,
                'razorpay_payment_id': data.razorpay_payment_id,
                'razorpay_signature': data.razorpay_signature
            })
        except Exception as e:
            raise HTTPException(status_code=400, detail="Razorpay signature verification failed")

    if data.amount > order.balance_amount:
        raise HTTPException(status_code=400, detail="Payment exceeds remaining balance.")
        
    payment = CateringPayment(
        customer_id=order.customer_id,
        catering_order_id=order.id,
        transaction_id=data.razorpay_payment_id,
        amount=data.amount,
        payment_type="BALANCE",
        payment_method=data.payment_method or "ONLINE",
        payment_status="SUCCESS"
    )
    db.add(payment)
    db.flush()
    
    # Recalculate order columns
    order.paid_amount += data.amount
    order.balance_amount = order.total_amount - order.paid_amount
    
    if order.balance_amount <= 0:
        order.payment_status = "PAID"
        order.balance_amount = 0
    else:
        order.payment_status = "PARTIALLY_PAID"
    
    db.commit()
    db.refresh(order)
    
    return {
        "success": True,
        "order_id": order.id,
        "payment_status": order.payment_status,
        "paid_amount": order.paid_amount,
        "balance_amount": order.balance_amount
    }

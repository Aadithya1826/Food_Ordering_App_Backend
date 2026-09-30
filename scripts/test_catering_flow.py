import os
import sys
import uuid
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, engine, Base
from app.models.catering import CateringSession, CateringOrder
from app.models.menu import CateringOrderMenu
from app.models.customer import Customer

# Create tables
Base.metadata.create_all(bind=engine)

def main():
    db = SessionLocal()
    
    # 1. Setup Data
    # Check if a customer exists, otherwise create
    cust = db.query(Customer).first()
    if not cust:
        cust = Customer(phone="9999999999", name="Test Customer")
        db.add(cust)
        db.commit()
        
    # Check if catering package exists
    pkg = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == "V-RICE MENU 1").first()
    if not pkg:
        pkg = CateringOrderMenu(
            name="VARIETY LUNCH/DINNER",
            code="V-RICE MENU 1",
            price=200.0,
            minimum_order_quantity=50,
            is_available=True,
            display_order=1
        )
        db.add(pkg)
        db.flush()
        
        # Add some items
        item1 = CateringOrderMenu(
            name="VARIETY LUNCH/DINNER",
            code="V-RICE MENU 1",
            price=0,
            category_name="Rice",
            item_name="Basmati Rice Pulav",
            customization_group="RICE",
            is_swappable=True,
            is_removable=True,
            remove_price=-10,
            same_group_replace_price=0,
            upgrade_group="BIRYANI",
            upgrade_price=50
        )
        item2 = CateringOrderMenu(
            name="VARIETY LUNCH/DINNER",
            code="V-RICE MENU 1",
            price=0,
            category_name="Rice",
            item_name="Bisibelebath",
            customization_group="RICE",
            is_swappable=True,
            is_removable=True,
            remove_price=-10,
            same_group_replace_price=0,
            upgrade_group="BIRYANI",
            upgrade_price=50
        )
        item3 = CateringOrderMenu(
            name="VARIETY LUNCH/DINNER", # This acts as master item available for add/replace
            price=0,
            category_name="Biryani",
            item_name="Veg Biryani",
            customization_group="BIRYANI",
            is_swappable=True,
            is_available=True,
            add_price=30
        )
        db.add_all([item1, item2, item3])
        db.commit()
    
    print("Test data ready.")
    
    # Run the fastapi app locally through a TestClient
    # pyrefly: ignore [missing-import]
    from fastapi.testclient import TestClient
    from app.main import app
    from app.utils.dependencies import get_current_customer
    
    # override dependency
    app.dependency_overrides[get_current_customer] = lambda: db.query(Customer).filter(Customer.id == cust.id).first()
    
    client = TestClient(app)
    
    print("Testing GET /catering/packages...")
    res = client.get("/api/v1/public/catering/packages")
    print(res.status_code, res.json())
    assert res.status_code == 200
    
    print("Testing GET /catering/packages/V-RICE MENU 1/items...")
    res = client.get("/api/v1/public/catering/packages/V-RICE MENU 1/items")
    print(res.status_code, res.json())
    assert res.status_code == 200
    
    print("Testing POST /catering/sessions...")
    res = client.post("/api/v1/public/catering/sessions", json={
        "customer_id": cust.id,
        "package_code": "V-RICE MENU 1",
        "guest_count": 100
    })
    print(res.status_code, res.json())
    assert res.status_code == 200
    session_id = res.json().get("session_id") or res.json().get("id")
    
    # We need an item ID to test replacements
    items = db.query(CateringOrderMenu).filter(CateringOrderMenu.code == "V-RICE MENU 1", CateringOrderMenu.item_name == "Basmati Rice Pulav").first()
    biryani = db.query(CateringOrderMenu).filter(CateringOrderMenu.item_name == "Veg Biryani").first()
    
    print("Testing POST customizations REPLACE...")
    res = client.post(f"/api/v1/public/catering/sessions/{session_id}/customizations", json={
        "action_type": "REPLACE",
        "original_item_id": items.id,
        "new_item_id": biryani.id
    })
    print(res.status_code, res.json())
    assert res.status_code == 200
    
    print("Testing POST quote...")
    res = client.post(f"/api/v1/public/catering/sessions/{session_id}/quote")
    print(res.status_code, res.json())
    assert res.status_code == 200
    
    # Before payment, we need event details
    client.patch(f"/api/v1/public/catering/sessions/{session_id}/event", json={
        "event_date": "2026-10-25",
        "serving_time": "12:30:00"
    })
    
    quote_res = res
    print("Testing POST payment verify...")
    txn = str(uuid.uuid4())
    res = client.post(f"/api/v1/public/catering/sessions/{session_id}/payment/verify", json={
        "transaction_id": txn,
        "amount": quote_res.json()["advance_amount"], # from quote
        "payment_method": "UPI"
    })
    print(res.status_code, res.json())
    assert res.status_code == 200
    
    # Idempotency Test
    print("Testing Idempotency on payment verify...")
    res2 = client.post(f"/api/v1/public/catering/sessions/{session_id}/payment/verify", json={
        "transaction_id": txn,
        "amount": quote_res.json()["advance_amount"],
        "payment_method": "UPI"
    })
    print(res2.status_code, res2.json())
    assert res2.status_code == 200
    assert "already created" in res2.json().get("message", "")
    
    print("Testing completed successfully.")
    
if __name__ == "__main__":
    main()

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.db import SessionLocal
from app.models.customer import Customer
from app.models.user import User
from app.models.restaurant import Restaurant
from app.models.catering import CateringSession, CateringOrder, CateringPayment
from app.utils.auth import create_token

client = TestClient(app)

def verify():
    db = SessionLocal()
    print("=" * 60)
    print("STARTING CATERING RESTAURANT_ID VERIFICATION")
    print("=" * 60)

    # Record historical baseline
    historical_sessions_count = db.query(CateringSession).count()
    historical_orders_count = db.query(CateringOrder).count()
    historical_null_sessions = db.query(CateringSession).filter(CateringSession.restaurant_id.is_(None)).count()
    historical_null_orders = db.query(CateringOrder).filter(CateringOrder.restaurant_id.is_(None)).count()

    print(f"Historical Baseline:")
    print(f"  Total Catering Sessions: {historical_sessions_count} (NULL: {historical_null_sessions})")
    print(f"  Total Catering Orders:   {historical_orders_count} (NULL: {historical_null_orders})")

    # Fetch users and customer
    cust = db.query(Customer).filter(Customer.id == 5).first()
    if not cust:
        cust = db.query(Customer).first()
    
    # MGR Nagar Admin (restaurant_id = 2)
    mgr_admin = db.query(User).filter(User.restaurant_id == 2, User.role == "HOTEL_ADMIN").first()
    # Mugalivakkam Admin (restaurant_id = 1)
    muga_admin = db.query(User).filter(User.restaurant_id == 1, User.role == "HOTEL_ADMIN").first()

    assert mgr_admin is not None, "MGR Nagar Admin not found"
    assert muga_admin is not None, "Mugalivakkam Admin not found"
    assert cust is not None, "Customer not found"

    cust_token = create_token(cust, account_type="CUSTOMER")
    cust_headers = {"Authorization": f"Bearer {cust_token}"}

    mgr_token = create_token(mgr_admin)
    mgr_headers = {"Authorization": f"Bearer {mgr_token}"}

    muga_token = create_token(muga_admin)
    muga_headers = {"Authorization": f"Bearer {muga_token}"}

    print("\n[STEP 1] Testing Invalid / Missing Restaurant IDs on Session Creation:")
    # Test 1A: missing restaurant_id
    res_missing = client.post("/api/v1/public/catering/sessions", json={
        "customer_id": cust.id,
        "package_code": "V-RICE MENU 1",
        "guest_count": 50
    }, headers=cust_headers)
    print(f"  Missing restaurant_id -> status: {res_missing.status_code} (Expected 422)")
    assert res_missing.status_code == 422

    # Test 1B: invalid restaurant_id = 9999
    res_invalid = client.post("/api/v1/public/catering/sessions", json={
        "customer_id": cust.id,
        "restaurant_id": 9999,
        "package_code": "V-RICE MENU 1",
        "guest_count": 50
    }, headers=cust_headers)
    print(f"  Invalid restaurant_id 9999 -> status: {res_invalid.status_code} (Expected 400: {res_invalid.json().get('detail')})")
    assert res_invalid.status_code == 400

    print("\n[STEP 2] Creating New Test Catering Session for restaurant_id = 2:")
    create_payload = {
        "customer_id": cust.id,
        "restaurant_id": 2,
        "package_code": "V-RICE MENU 1",
        "guest_count": 50
    }
    res_create = client.post("/api/v1/public/catering/sessions", json=create_payload, headers=cust_headers)
    print(f"  API Request: POST /api/v1/public/catering/sessions")
    print(f"  Payload: {create_payload}")
    print(f"  Response Status: {res_create.status_code}")
    print(f"  Response Body: {res_create.json()}")
    assert res_create.status_code == 200, res_create.text
    session_data = res_create.json()
    test_session_id = session_data.get("session_id") or session_data.get("id")

    # SQL Verification 1
    sql_session = db.execute(text("SELECT id, customer_id, restaurant_id, package_code, status FROM catering_sessions WHERE id = :sid"), {"sid": str(test_session_id)}).fetchone()
    print(f"\n>>> SQL VERIFICATION 1: catering_sessions.restaurant_id")
    print(f"    SQL Query: SELECT id, customer_id, restaurant_id, package_code, status FROM catering_sessions WHERE id = '{test_session_id}'")
    print(f"    Result: {sql_session}")
    assert sql_session.restaurant_id == 2, f"Expected restaurant_id == 2, got {sql_session.restaurant_id}"
    print(f"    VERIFIED: catering_sessions.restaurant_id = {sql_session.restaurant_id} (PASSED)")

    # Update event details & generate quote
    patch_payload = {
        "event_name": "Antigravity MGR Nagar Celebration",
        "event_date": "2026-11-20",
        "serving_time": "13:00",
        "occasion": "Corporate Launch",
        "service_type": "Buffet"
    }
    client.patch(f"/api/v1/public/catering/sessions/{test_session_id}/event", json=patch_payload, headers=cust_headers)
    res_quote = client.post(f"/api/v1/public/catering/sessions/{test_session_id}/quote", headers=cust_headers)
    advance_amount = res_quote.json()["advance_amount"]

    print("\n[STEP 3] Verified Payment & Order Creation:")
    test_payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"
    test_order_id = f"order_test_{uuid.uuid4().hex[:12]}"
    verify_payload = {
        "amount": advance_amount,
        "razorpay_payment_id": test_payment_id,
        "razorpay_order_id": test_order_id,
        "razorpay_signature": "mock_signature",
        "payment_method": "CASH"
    }
    res_verify = client.post(f"/api/v1/public/catering/sessions/{test_session_id}/payment/verify", json=verify_payload, headers=cust_headers)
    print(f"  API Request: POST /api/v1/public/catering/sessions/{test_session_id}/payment/verify")
    print(f"  Response Status: {res_verify.status_code}")
    print(f"  Response Body: {res_verify.json()}")
    assert res_verify.status_code == 200, res_verify.text
    order_id = res_verify.json()["order_id"]

    # Test idempotency (duplicate prevention)
    res_duplicate = client.post(f"/api/v1/public/catering/sessions/{test_session_id}/payment/verify", json=verify_payload, headers=cust_headers)
    print(f"  Duplicate verify response: {res_duplicate.json()}")
    assert res_duplicate.status_code == 200
    assert "already created" in res_duplicate.json()["message"]

    # SQL Verification 2
    sql_order = db.execute(text("SELECT id, catering_session_id, customer_id, restaurant_id, package_name, order_status, paid_amount FROM catering_orders WHERE id = :oid"), {"oid": order_id}).fetchone()
    print(f"\n>>> SQL VERIFICATION 2: catering_orders.restaurant_id")
    print(f"    SQL Query: SELECT id, catering_session_id, customer_id, restaurant_id, package_name, order_status, paid_amount FROM catering_orders WHERE id = {order_id}")
    print(f"    Result: {sql_order}")
    assert sql_order.restaurant_id == 2, f"Expected restaurant_id == 2, got {sql_order.restaurant_id}"
    print(f"    VERIFIED: catering_orders.restaurant_id = {sql_order.restaurant_id} (PASSED)")

    print("\n[STEP 4] Admin Panel Access Isolation:")
    # 4A. MGR Nagar admin retrieval
    res_mgr_list = client.get("/api/v1/admin/catering/orders", headers=mgr_headers)
    mgr_order_ids = [o["id"] for o in res_mgr_list.json()]
    print(f"  MGR Nagar Admin (rest_id=2) orders list: {mgr_order_ids}")
    assert order_id in mgr_order_ids, f"Order {order_id} should be visible to MGR Nagar admin"

    res_mgr_detail = client.get(f"/api/v1/admin/catering/orders/{order_id}", headers=mgr_headers)
    print(f"  MGR Nagar Admin GET /orders/{order_id} status: {res_mgr_detail.status_code}")
    assert res_mgr_detail.status_code == 200, "MGR Nagar admin should be able to retrieve order details"
    print(f"    VERIFIED: MGR Nagar admin can retrieve the order (PASSED)")

    # 4B. Mugalivakkam admin retrieval
    res_muga_list = client.get("/api/v1/admin/catering/orders", headers=muga_headers)
    muga_order_ids = [o["id"] for o in res_muga_list.json()]
    print(f"  Mugalivakkam Admin (rest_id=1) orders list: {muga_order_ids}")
    assert order_id not in muga_order_ids, f"Order {order_id} must NOT be visible to Mugalivakkam admin"

    res_muga_detail = client.get(f"/api/v1/admin/catering/orders/{order_id}", headers=muga_headers)
    print(f"  Mugalivakkam Admin GET /orders/{order_id} status: {res_muga_detail.status_code} (detail: {res_muga_detail.json()})")
    assert res_muga_detail.status_code == 403, f"Expected 403 Forbidden, got {res_muga_detail.status_code}"
    print(f"    VERIFIED: Mugalivakkam admin cannot retrieve the order (PASSED)")

    # 4C. Mugalivakkam admin update attempt
    res_muga_patch = client.patch(f"/api/v1/admin/catering/orders/{order_id}/status", json={"order_status": "PREPARING"}, headers=muga_headers)
    print(f"  Mugalivakkam Admin PATCH /orders/{order_id}/status status: {res_muga_patch.status_code}")
    assert res_muga_patch.status_code == 403
    print(f"    VERIFIED: Mugalivakkam admin cannot alter order status (PASSED)")

    print("\n[STEP 5] Historical Data Integrity Verification:")
    current_null_sessions = db.query(CateringSession).filter(CateringSession.restaurant_id.is_(None)).count()
    current_null_orders = db.query(CateringOrder).filter(CateringOrder.restaurant_id.is_(None)).count()
    print(f"  Historical NULL Sessions count: {current_null_sessions} (Baseline was: {historical_null_sessions})")
    print(f"  Historical NULL Orders count:   {current_null_orders} (Baseline was: {historical_null_orders})")
    assert current_null_sessions == historical_null_sessions, "Historical NULL sessions changed!"
    assert current_null_orders == historical_null_orders, "Historical NULL orders changed!"
    print(f"    VERIFIED: Existing historical records remain unchanged (PASSED)")

    print("\n" + "=" * 60)
    print("ALL 5 VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 60)
    db.close()

if __name__ == "__main__":
    verify()

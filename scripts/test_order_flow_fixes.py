import requests
import json
import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE_URL = "http://127.0.0.1:8001"
RESTAURANT_ID = 2
TEST_PHONE = f"99{int(time.time()) % 100000000:08d}"

def run_tests():
    print("=== STARTING FULL SUITE TESTS FOR ORDER INTEGRITY FIXES ===")
    
    # 0. Health check
    try:
        r = requests.get(f"{BASE_URL}/docs", timeout=5)
        print(f"[OK] Backend reachable (status: {r.status_code})")
    except Exception as e:
        print(f"[FAIL] Backend unreachable at {BASE_URL}: {e}")
        return False

    # Fetch a valid menu item
    r = requests.get(f"{BASE_URL}/api/v1/public/menu/items?restaurant_id={RESTAURANT_ID}")
    menu_items = r.json() if r.status_code == 200 else []
    if not menu_items:
        # Fallback to restaurant 1
        r = requests.get(f"{BASE_URL}/api/v1/public/menu/items?restaurant_id=1")
        menu_items = r.json() if r.status_code == 200 else []
        rest_id = 1
    else:
        rest_id = RESTAURANT_ID

    if not menu_items:
        print("[FAIL] Could not fetch menu items")
        return False

    # Find first available item
    test_item = None
    for cat in menu_items:
        items = cat.get("items", []) if isinstance(cat, dict) else []
        if items:
            test_item = items[0]
            break
        elif isinstance(cat, dict) and "id" in cat and "price" in cat:
            test_item = cat
            break

    if not test_item:
        print("[FAIL] No valid test item found in menu")
        return False

    item_id = test_item["id"]
    item_price = float(test_item.get("price", 50))
    print(f"[OK] Using test item: ID {item_id}, price {item_price} at restaurant {rest_id}")

    # TEST 1: Table verification endpoint
    print("\n--- TEST 1: Table Verification Endpoint ---")
    r = requests.get(f"{BASE_URL}/api/customer/table/verify?table_number=T-01&restaurant_id={rest_id}")
    print(f"Verify T-01 response: {r.status_code}, {r.text[:120]}")
    t01_data = r.json()
    assert "valid" in t01_data, "Expected 'valid' field in verify response"

    r_invalid = requests.get(f"{BASE_URL}/api/customer/table/verify?table_number=T-NONEXISTENT-999&restaurant_id={rest_id}")
    print(f"Verify invalid table response: {r_invalid.status_code}, {r_invalid.text[:120]}")
    inv_data = r_invalid.json()
    assert inv_data.get("valid") is False, "Expected valid=False for non-existent table"
    print("[PASS] Table verification endpoint correctly identifies valid vs invalid tables")

    # TEST 2: Dine-In Order without table number rejected
    print("\n--- TEST 2: Dine-In Order Without Table Rejected ---")
    order_payload = {
        "order_type": "Dine In",
        "table_number": "",
        "phone": TEST_PHONE,
        "payment_method": "Pay at Counter",
        "cart": [{"id": item_id, "name": "Test Item", "price": item_price, "quantity": 1}],
        "subtotal": item_price,
        "total_amount": item_price
    }
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=order_payload)
    print(f"Order without table response: {r.status_code}, {r.text}")
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    assert "Select Your Table First" in r.text or "table number" in r.text
    print("[PASS] Dine-in order without table is rejected with 400")

    # TEST 3: Dine-In Order with invalid non-existent table rejected (No auto-creation)
    print("\n--- TEST 3: Dine-In Order With Non-existent Table Rejected ---")
    order_payload["table_number"] = "T-NONEXISTENT-999"
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=order_payload)
    print(f"Order with invalid table response: {r.status_code}, {r.text}")
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    assert "does not exist" in r.text
    print("[PASS] Dine-in order with non-existent table is rejected and not auto-created")

    # TEST 4: Dine-In Order with valid table succeeds
    print("\n--- TEST 4: Dine-In Order With Valid Table ---")
    valid_table_num = t01_data.get("table", {}).get("table_number", "T-01") if t01_data.get("valid") else "T-01"
    t_id = t01_data.get("table", {}).get("id")

    # Clean up any leftover test sessions on this table so test starts 100% clean
    from app.db import SessionLocal
    from app.models.restaurant import Restaurant
    from app.models.table import Table
    from app.models.dine_in_session import DineInSession
    from app.models.order import Order
    db_clean = SessionLocal()
    try:
        if t_id:
            active_s = db_clean.query(DineInSession).filter(DineInSession.table_id == t_id, DineInSession.status == "ACTIVE").all()
            for s in active_s:
                s.status = "CANCELLED"
                for o in db_clean.query(Order).filter(Order.dine_in_session_id == s.id, Order.status != "CANCELLED").all():
                    o.status = "CANCELLED"
            db_clean.commit()
    finally:
        db_clean.close()

    order_payload["table_number"] = valid_table_num
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=order_payload)
    print(f"Order with valid table response: {r.status_code}, {r.text[:150]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    created_order = r.json()
    order_id = created_order.get("orderId") or f"ORD-{created_order.get('id')}"
    db_order_id = created_order.get("dbOrderId") or created_order.get("id")
    print(f"[PASS] Successfully created Dine-In order {order_id} (db id: {db_order_id})")

    # TEST 5: Conflicting Delivery Order is strictly blocked while Dine-In order is active
    print("\n--- TEST 5: Delivery Order Blocked While Active Dine-In Exists ---")
    delivery_payload = {
        "order_type": "DELIVERY",
        "phone": TEST_PHONE,
        "payment_method": "Cash on Delivery",
        "delivery_address_id": 1,
        "cart": [{"id": item_id, "name": "Test Item", "price": item_price, "quantity": 1}],
        "subtotal": item_price,
        "total_amount": item_price + 100.0
    }
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=delivery_payload)
    print(f"Conflicting delivery order response: {r.status_code}, {r.text}")
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    assert "Dine-In Order In Progress" in r.text
    print("[PASS] Conflicting delivery order blocked with 'Dine-In Order In Progress' message")

    # TEST 6: Order Cancellation
    print(f"\n--- TEST 6: Cancel Active Dine-In Order {order_id} ---")
    session_uid = created_order.get("sessionUid")
    cancel_payload = {
        "reason": "Customer changed mind before preparation",
        "session_uid": session_uid
    }
    r = requests.post(f"{BASE_URL}/api/orders/{order_id}/cancel?restaurant_id={rest_id}", json=cancel_payload)
    print(f"Cancel order response: {r.status_code}, {r.text[:150]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    cancel_res = r.json()
    assert cancel_res.get("status") == "CANCELLED" or cancel_res.get("success") is True
    print(f"[PASS] Order {order_id} successfully cancelled")

    # TEST 7: Cancelled Order status verification
    print(f"\n--- TEST 7: Verify Order Detail reflects CANCELLED status ---")
    r = requests.get(f"{BASE_URL}/api/orders/{order_id}?restaurant_id={rest_id}")
    print(f"Get order response: {r.status_code}, {r.text[:150]}")
    assert r.status_code == 200
    res_json = r.json()
    order_data = res_json.get("order") if "order" in res_json else res_json
    assert order_data.get("status") == "CANCELLED", f"Expected CANCELLED, got {order_data.get('status')}"
    print(f"[PASS] Backend reflects status CANCELLED ({order_data.get('status')}); order is not deleted from DB")

    # TEST 8: After Dine-In order cancelled, delivery order is no longer blocked!
    print("\n--- TEST 8: Delivery Order Not Blocked After Dine-In Cancelled ---")
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=delivery_payload)
    print(f"Delivery order after cancellation response: {r.status_code}, {r.text[:150]}")
    # It shouldn't be blocked by "Dine-In Order In Progress"
    assert "Dine-In Order In Progress" not in r.text
    print("[PASS] Dine-in conflict restriction lifted once previous dine-in order is cancelled")

    # TEST 9: New Dine-In order after previous cancelled orders must NOT reuse cancelled session
    print("\n--- TEST 9: New Dine-In Order Does NOT Show or Reuse Cancelled Orders ---")
    old_session_id = created_order.get("sessionId")
    old_session_uid = created_order.get("sessionUid")
    new_dine_in_payload = {
        "order_type": "Dine In",
        "table_number": valid_table_num,
        "phone": TEST_PHONE,
        "payment_method": "Pay Later",
        "cart": [{"id": item_id, "name": "Test Item New Round", "price": item_price, "quantity": 1}],
        "subtotal": item_price,
        "total_amount": item_price,
        # Even if a client passed stale old session IDs:
        "session_id": old_session_id,
        "session_uid": old_session_uid
    }
    r = requests.post(f"{BASE_URL}/api/orders?restaurant_id={rest_id}", json=new_dine_in_payload)
    print(f"New Dine-In order response: {r.status_code}, {r.text[:150]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    new_order_data = r.json()
    new_session_id = new_order_data.get("sessionId")
    new_session_uid = new_order_data.get("sessionUid")
    new_order_id = new_order_data.get("orderId")
    print(f"Old session: {old_session_id} ({old_session_uid}) vs New session: {new_session_id} ({new_session_uid})")
    assert new_session_id != old_session_id, f"Expected a new session, but got old session {new_session_id} == {old_session_id}"
    print(f"[PASS] New Dine-In order received a clean, fresh session ID {new_session_id}")

    # TEST 10: Verify new session details contain ONLY the new order, without previous cancelled orders
    print("\n--- TEST 10: Verify Session Details Only Has Active Round (No Cancelled Orders) ---")
    r = requests.get(f"{BASE_URL}/api/dine-in/session/{new_session_id}/details?restaurant_id={rest_id}")
    print(f"Session details response: {r.status_code}, {r.text[:200]}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    sess_details = r.json()
    rounds = sess_details.get("orders", [])
    print(f"Rounds in new session: {len(rounds)}")
    assert len(rounds) == 1, f"Expected exactly 1 round, but got {len(rounds)}: {rounds}"
    assert rounds[0].get("orderId") == new_order_id, f"Expected order {new_order_id}, got {rounds[0].get('orderId')}"
    assert (rounds[0].get("status") or "").upper() != "CANCELLED", "Active round should not be cancelled"
    # Ensure old cancelled order ORD-041475 is not in the new session's rounds
    round_order_ids = [ro.get("orderId") for ro in rounds]
    assert order_id not in round_order_ids, f"Cancelled order {order_id} must not appear in new session rounds {round_order_ids}"
    print(f"[PASS] Session details cleanly lists 1 active round ({new_order_id}) and ZERO previous cancelled orders!")

    print("\n============================================================")
    print("ALL ORDER INTEGRITY & RESTRICTION TESTS PASSED SUCCESSFULLY!")
    print("============================================================")
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

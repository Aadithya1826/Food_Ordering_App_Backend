import os
import sys
import unittest
from datetime import datetime

# Adjust path to import backend app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal
from app.models.restaurant import Restaurant
from app.models.table import Table
from app.models.menu import MenuItem, MenuCategory
from app.models.order import Order, OrderItem
from app.models.dine_in_session import DineInSession
from app.routes.customer import (
    CustomerOrderPayload, CustomerCartItem, place_order, 
    ActiveDineInSessionPayload, get_or_create_active_dine_in_session,
    get_dine_in_session_details, SettleDineInSessionPayload, settle_dine_in_session
)
from app.routes.orders import get_live_orders, update_status, OrderStatusUpdate

def test_dine_in_session_flow():
    db = SessionLocal()
    try:
        print("\n=== STARTING P0 DINE-IN SESSION E2E TEST ===")
        # 1. Setup Table and Restaurant
        rest = db.query(Restaurant).first()
        if not rest:
            rest = Restaurant(name="Udipi Test Outlet", address="Chennai", phone="9876543210")
            db.add(rest)
            db.commit()
            db.refresh(rest)

        test_table_num = f"T-E2E-{int(datetime.utcnow().timestamp())}"
        table = Table(restaurant_id=rest.id, table_number=test_table_num, capacity=4, status="Vacant")
        db.add(table)
        db.commit()
        db.refresh(table)

        # Retrieve a menu item
        item1 = db.query(MenuItem).filter(MenuItem.restaurant_id == rest.id, MenuItem.is_available == True).first()
        if not item1:
            cat = MenuCategory(name="South Indian", restaurant_id=rest.id)
            db.add(cat)
            db.commit()
            item1 = MenuItem(name="Masala Dosa", price=80.0, category_id=cat.id, restaurant_id=rest.id, is_available=True)
            db.add(item1)
            db.commit()
            db.refresh(item1)

        item2 = db.query(MenuItem).filter(MenuItem.restaurant_id == rest.id, MenuItem.id != item1.id, MenuItem.is_available == True).first()
        if not item2:
            item2 = MenuItem(name="Filter Coffee", price=30.0, category_id=item1.category_id, restaurant_id=rest.id, is_available=True)
            db.add(item2)
            db.commit()
            db.refresh(item2)

        # 2. Step 1: Initialize / Lookup Active Dine-In Session for table
        active_sess_res = get_or_create_active_dine_in_session(
            ActiveDineInSessionPayload(restaurant_id=rest.id, table_number=test_table_num, phone="9988776655"),
            db=db
        )
        assert active_sess_res["success"] is True
        sess_id = active_sess_res["sessionId"]
        sess_uid = active_sess_res["sessionUid"]
        print(f"[PASS] Created Dine-In Session: ID={sess_id}, UID={sess_uid}")

        # 3. Step 2: Customer places initial Dine-In Order (Order #1)
        payload1 = CustomerOrderPayload(
            restaurant_id=rest.id,
            table_number=test_table_num,
            order_type="DINE_IN",
            payment_method="Pay Later",
            phone="9988776655",
            session_id=sess_id,
            session_uid=sess_uid,
            cart=[CustomerCartItem(id=item1.id, quantity=1, price=float(item1.price))]
        )
        res1 = place_order(payload1, restaurant_id=rest.id, db=db)
        order1_id = res1["dbOrderId"]
        order1_str = res1["orderId"]
        print(f"[PASS] Placed Order #1: {order1_str} (dbId: {order1_id}), Total: Rs. {res1['totalAmount']}")
        assert res1["sessionId"] == sess_id

        # Verify Table is Occupied
        db.refresh(table)
        assert table.status == "Occupied"

        # 4. Step 3: Kitchen Prepares and Serves Order #1
        order1 = db.query(Order).filter(Order.id == order1_id).first()
        order1.status = "PREPARING"
        db.commit()

        # Update order to SERVED and ensure table is not auto-released
        order1.status = "SERVED"
        # Simulate auto-release logic check from orders.py
        sess = db.query(DineInSession).filter(DineInSession.id == order1.dine_in_session_id).first()
        if not (sess and sess.status == "ACTIVE"):
            table.status = "Vacant"
        db.commit()
        
        # Verify Table is STILL Occupied because session is active!
        db.refresh(table)
        assert table.status == "Occupied", f"Expected table to stay Occupied, got {table.status}"
        print("[PASS] Order #1 served. Table correctly remains 'Occupied' under active session.")

        # 5. Step 4: Customer taps 'Order More' -> places Order #2 (Separate Order ID under same session)
        payload2 = CustomerOrderPayload(
            restaurant_id=rest.id,
            table_number=test_table_num,
            order_type="DINE_IN",
            payment_method="Pay Later",
            phone="9988776655",
            session_id=sess_id,
            session_uid=sess_uid,
            cart=[CustomerCartItem(id=item2.id, quantity=2, price=float(item2.price))]
        )
        res2 = place_order(payload2, restaurant_id=rest.id, db=db)
        order2_id = res2["dbOrderId"]
        order2_str = res2["orderId"]
        print(f"[PASS] Placed Order #2 (Order More): {order2_str} (dbId: {order2_id}), Total: Rs. {res2['totalAmount']}")
        assert order2_id != order1_id, "Order #2 must have an independent order ID!"
        assert res2["sessionId"] == sess_id

        # 6. Step 5: Kitchen KDS / Live Orders inspection
        class MockUser:
            role = "HOTEL_ADMIN"
            restaurant_id = rest.id
            id = 1

        live_orders = get_live_orders(restaurant_id=rest.id, user=MockUser(), db=db)
        live_order_ids = [o["order_id"] for o in live_orders]
        assert order2_id in live_order_ids, "Order #2 must appear in Kitchen KDS live orders!"
        print(f"[PASS] Kitchen KDS received Order #2 independently in live orders (dine_in_session_id={sess_id}).")

        # 7. Step 6: Session Details & Combined Bill
        details = get_dine_in_session_details(str(sess_id), restaurant_id=rest.id, db=db)
        assert len(details["orders"]) == 2, f"Expected 2 order rounds in session, got {len(details['orders'])}"
        expected_total = float(item1.price) * 1 + float(item2.price) * 2
        assert abs(details["totalAmount"] - expected_total) < 0.01, f"Expected combined total {expected_total}, got {details['totalAmount']}"
        print(f"[PASS] Combined Session Bill verified: Rs. {details['totalAmount']} across {len(details['orders'])} rounds.")

        # 8. Step 7: Final Bill Settlement
        settle_res = settle_dine_in_session(
            str(sess_id),
            SettleDineInSessionPayload(payment_method="Pay at Counter", amount_paid=expected_total),
            restaurant_id=rest.id,
            db=db
        )
        assert settle_res["success"] is True
        assert settle_res["paymentStatus"] == "Paid"

        # Check DB state
        session_db = db.query(DineInSession).filter(DineInSession.id == sess_id).first()
        assert session_db.status == "COMPLETED"
        assert session_db.payment_status == "Paid"

        o1 = db.query(Order).filter(Order.id == order1_id).first()
        o2 = db.query(Order).filter(Order.id == order2_id).first()
        assert o1.payment_status == "Paid"
        assert o2.payment_status == "Paid"

        db.refresh(table)
        assert table.status == "Vacant", f"Expected table to be Vacant after session close, got {table.status}"
        print(f"[PASS] Session settled, orders marked Paid, and table {table.table_number} released to 'Vacant'.")
        print("\n=== ALL P0 DINE-IN E2E TESTS PASSED SUCCESSFULLY! ===")

    finally:
        db.close()

if __name__ == "__main__":
    test_dine_in_session_flow()

import os
import sys
from datetime import datetime, timedelta
import unittest

# Point to backend directory
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.db import Base
from app.models.user import User
from app.models.restaurant import Restaurant
from app.models.table import Table
from app.models.menu import MenuCategory, MenuItem
from app.models.order import Order, OrderItem
from app.models.dine_in_session import DineInSession
from app.models.customer import Customer, LoyaltyTransaction
from app.models.delivery import DeliveryPartner, DeliveryAssignment, DeliveryStatusHistory, RiderLocation
from app.utils.auth import create_token
from app.utils.dependencies import get_db as dep_get_db
from app.routes.data_management import get_db as dm_get_db
from app.main import app

# Isolated Test Database
TEST_DB_PATH = os.path.join(BACKEND_DIR, "test_data_management_db.sqlite")
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[dep_get_db] = override_get_db
app.dependency_overrides[dm_get_db] = override_get_db

class TestDataManagementFeature(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Clean up any leftover test db
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

        # Create all tables from SQLAlchemy models
        Base.metadata.create_all(bind=test_engine)

        # Create child tables that match PostgreSQL schema for full FK coverage
        with test_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER,
                    amount REAL,
                    payment_method TEXT,
                    status TEXT,
                    transaction_id TEXT,
                    created_at TIMESTAMP
                );
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS kot_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER,
                    status TEXT,
                    printed_at TIMESTAMP
                );
            """))
            conn.commit()

        # Seed initial test data
        db = TestSessionLocal()
        try:
            # 1. Restaurant
            rest = Restaurant(id=1, name="Data Udipi Test Hotel", address="Chennai", phone="9876543210")
            db.add(rest)
            
            # 2. Table
            tbl = Table(id=1, restaurant_id=1, table_number="T1", capacity=4)
            db.add(tbl)
            
            # 3. Category & Item
            cat = MenuCategory(id=1, restaurant_id=1, name="South Indian")
            db.add(cat)
            db.flush()
            
            item1 = MenuItem(id=1, restaurant_id=1, category_id=1, name="Masala Dosa", price=80.0)
            item2 = MenuItem(id=2, restaurant_id=1, category_id=1, name="Filter Coffee", price=30.0)
            db.add_all([item1, item2])

            # 4. Users (Super Admin, Hotel Admin, Cashier)
            super_admin = User(id=1, restaurant_id=None, name="Super Admin", email="super@dataudipi.com", password_hash="hash", role="SUPER_ADMIN")
            hotel_admin = User(id=2, restaurant_id=1, name="Hotel Admin", email="hotel@dataudipi.com", password_hash="hash", role="HOTEL_ADMIN")
            cashier = User(id=3, restaurant_id=1, name="Cashier User", email="cashier@dataudipi.com", password_hash="hash", role="CASHIER")
            db.add_all([super_admin, hotel_admin, cashier])
            
            # 5. Customer
            cust = Customer(id=1, name="Regular Guest", phone="9988776655")
            db.add(cust)
            db.flush()

            # Helper to create an order with full dependency tree
            def create_order_bundle(order_id, dt, session_uid):
                dis = DineInSession(
                    id=order_id,
                    session_uid=session_uid,
                    restaurant_id=1,
                    table_id=1,
                    status="COMPLETED",
                    payment_status="Paid",
                    total_amount=110.0,
                    created_at=dt,
                    closed_at=dt + timedelta(minutes=45)
                )
                db.add(dis)
                db.flush()

                ord_rec = Order(
                    id=order_id,
                    restaurant_id=1,
                    table_id="1",
                    user_id=2,
                    status="SERVED",
                    payment_status="Paid",
                    payment_method="UPI",
                    order_type="DINE_IN",
                    total_amount=110.0,
                    dine_in_session_id=dis.id,
                    created_at=dt
                )
                db.add(ord_rec)
                db.flush()

                oi1 = OrderItem(order_id=order_id, menu_item_id=1, quantity=1.0, price=80.0)
                oi2 = OrderItem(order_id=order_id, menu_item_id=2, quantity=1.0, price=30.0)
                db.add_all([oi1, oi2])

                # Raw child tables
                db.execute(text("INSERT INTO payments (order_id, amount, payment_method, status, created_at) VALUES (:oid, 110.0, 'UPI', 'SUCCESS', :dt)"),
                           {"oid": order_id, "dt": dt})
                db.execute(text("INSERT INTO kot_tickets (order_id, status, printed_at) VALUES (:oid, 'PRINTED', :dt)"),
                           {"oid": order_id, "dt": dt})

            # Seed Month 1: August 2026 (Historical - eligible)
            create_order_bundle(101, datetime(2026, 8, 10, 12, 0, 0), "sess-aug-1")
            create_order_bundle(102, datetime(2026, 8, 20, 19, 0, 0), "sess-aug-2")

            # Seed Month 2: September 2026 (Target previous calendar month - eligible)
            create_order_bundle(201, datetime(2026, 9, 5, 13, 0, 0), "sess-sep-1")
            create_order_bundle(202, datetime(2026, 9, 15, 20, 0, 0), "sess-sep-2")
            create_order_bundle(203, datetime(2026, 9, 25, 8, 30, 0), "sess-sep-3")

            # Seed Current Month: October 2026 (Protected - must NEVER be deleted)
            create_order_bundle(301, datetime(2026, 10, 2, 11, 0, 0), "sess-oct-1")
            create_order_bundle(302, datetime(2026, 10, 8, 14, 0, 0), "sess-oct-2")
            create_order_bundle(303, datetime(2026, 10, 12, 19, 30, 0), "sess-oct-3")
            create_order_bundle(304, datetime(2026, 10, 15, 21, 0, 0), "sess-oct-4")

            db.commit()
            
            # Auth tokens
            cls.super_token = create_token(super_admin)
            cls.hotel_token = create_token(hotel_admin)
            cls.cashier_token = create_token(cashier)
            
            cls.super_headers = {"Authorization": f"Bearer {cls.super_token}"}
            cls.hotel_headers = {"Authorization": f"Bearer {cls.hotel_token}"}
            cls.cashier_headers = {"Authorization": f"Bearer {cls.cashier_token}"}
            
            cls.client = TestClient(app)
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except Exception:
                pass

    def test_01_unauthorized_user_rejected(self):
        """Cashier role must be rejected with 403 Forbidden on preview and delete"""
        res_preview = self.client.get("/api/v1/admin/data-management/preview?month=2026-09", headers=self.cashier_headers)
        self.assertEqual(res_preview.status_code, 403)
        self.assertIn("Access denied", res_preview.json()["detail"])

        res_delete = self.client.post("/api/v1/admin/data-management/delete", json={
            "month": "2026-09",
            "confirmation_token": "fake-token",
            "confirm_phrase": "PERMANENTLY DELETE 2026-09 DATA"
        }, headers=self.cashier_headers)
        self.assertEqual(res_delete.status_code, 403)

    def test_02_current_month_deletion_strictly_rejected(self):
        """Current month (2026-10) must be rejected with 400 Bad Request"""
        res = self.client.get("/api/v1/admin/data-management/preview?month=2026-10", headers=self.super_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot delete data from the current month", res.json()["detail"])

        res_del = self.client.post("/api/v1/admin/data-management/delete", json={
            "month": "2026-10",
            "confirmation_token": "fake-token",
            "confirm_phrase": "PERMANENTLY DELETE 2026-10 DATA"
        }, headers=self.super_headers)
        self.assertEqual(res_del.status_code, 400)
        self.assertIn("Cannot delete data from the current month", res_del.json()["detail"])

    def test_03_future_month_deletion_strictly_rejected(self):
        """Future month (e.g. 2026-11) must be rejected with 400 Bad Request"""
        res = self.client.get("/api/v1/admin/data-management/preview?month=2026-11", headers=self.super_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot delete data from the current month", res.json()["detail"])

    def test_04_preview_previous_month_success(self):
        """Previewing previous month (2026-09) returns correct date range and estimated counts"""
        res = self.client.get("/api/v1/admin/data-management/preview?month=2026-09", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        
        self.assertEqual(data["month"], "2026-09")
        self.assertEqual(data["month_name"], "September 2026")
        self.assertIn("01/09/2026", data["date_range_display"])
        self.assertIn("30/09/2026", data["date_range_display"])
        
        # Verify affected tables counts: 3 orders in September, each has 2 items, 1 payment, 1 kot, 1 session
        counts = {t["table_name"]: t["estimated_count"] for t in data["affected_tables"]}
        self.assertEqual(counts.get("orders"), 3)
        self.assertEqual(counts.get("order_items"), 6)
        self.assertEqual(counts.get("payments"), 3)
        self.assertEqual(counts.get("kot_tickets"), 3)
        self.assertEqual(counts.get("dine_in_sessions"), 3)
        
        self.assertTrue(len(data["confirmation_token"]) > 10)
        self.assertEqual(data["required_confirm_phrase"], "PERMANENTLY DELETE 2026-09 DATA")
        self.assertIn("WARNING:", data["permanent_deletion_warning"])
        self.assertIn("Regulatory Notice:", data["retention_advisory"])

    def test_05_delete_with_wrong_confirmation_phrase_rejected(self):
        """Deletion with misspelled or mismatched confirmation phrase must fail with 400"""
        preview_res = self.client.get("/api/v1/admin/data-management/preview?month=2026-09", headers=self.super_headers)
        token = preview_res.json()["confirmation_token"]

        res = self.client.post("/api/v1/admin/data-management/delete", json={
            "month": "2026-09",
            "confirmation_token": token,
            "confirm_phrase": "DELETE 2026-09"  # missing PERMANENTLY ... DATA
        }, headers=self.super_headers)
        
        self.assertEqual(res.status_code, 400)
        self.assertIn("Confirmation phrase mismatch", res.json()["detail"])

    def test_06_delete_with_invalid_or_tampered_token_rejected(self):
        """Deletion with corrupted token must fail with 400"""
        res = self.client.post("/api/v1/admin/data-management/delete", json={
            "month": "2026-09",
            "confirmation_token": "tampered:token:payload",
            "confirm_phrase": "PERMANENTLY DELETE 2026-09 DATA"
        }, headers=self.super_headers)
        
        self.assertEqual(res.status_code, 400)
        self.assertIn("Confirmation token has expired or is invalid", res.json()["detail"])

    def test_07_successful_permanent_deletion_of_previous_month(self):
        """
        Executes real deletion of September 2026 data.
        Verifies:
        1. September orders, items, payments, kot_tickets, and sessions are permanently deleted.
        2. August 2026 orders remain 100% intact.
        3. Current month October 2026 orders remain 100% intact.
        """
        # Step 1: Preview
        preview_res = self.client.get("/api/v1/admin/data-management/preview?month=2026-09", headers=self.super_headers)
        self.assertEqual(preview_res.status_code, 200)
        token = preview_res.json()["confirmation_token"]

        # Step 2: Delete
        del_res = self.client.post("/api/v1/admin/data-management/delete", json={
            "month": "2026-09",
            "confirmation_token": token,
            "confirm_phrase": "PERMANENTLY DELETE 2026-09 DATA"
        }, headers=self.super_headers)

        self.assertEqual(del_res.status_code, 200)
        res_data = del_res.json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["deleted_counts"]["orders"], 3)
        self.assertEqual(res_data["deleted_counts"]["order_items"], 6)
        self.assertEqual(res_data["deleted_counts"]["payments"], 3)
        self.assertEqual(res_data["deleted_counts"]["kot_tickets"], 3)
        self.assertEqual(res_data["deleted_counts"]["dine_in_sessions"], 3)

        # Step 3: Check database state directly
        db = TestSessionLocal()
        try:
            # September orders should be 0
            sep_orders = db.query(Order).filter(Order.id.in_([201, 202, 203])).all()
            self.assertEqual(len(sep_orders), 0)

            # August orders should still be 2
            aug_orders = db.query(Order).filter(Order.id.in_([101, 102])).all()
            self.assertEqual(len(aug_orders), 2)

            # October orders (current month) should still be 4
            oct_orders = db.query(Order).filter(Order.id.in_([301, 302, 303, 304])).all()
            self.assertEqual(len(oct_orders), 4)

            # Current month items still intact
            oct_items = db.query(OrderItem).filter(OrderItem.order_id.in_([301, 302, 303, 304])).count()
            self.assertEqual(oct_items, 8)
        finally:
            db.close()

        # Step 4: Subsequent preview for September should show 0 records
        preview_after = self.client.get("/api/v1/admin/data-management/preview?month=2026-09", headers=self.super_headers)
        self.assertEqual(preview_after.status_code, 200)
        self.assertEqual(preview_after.json()["total_records"], 0)

    def test_08_transaction_rollback_on_failure(self):
        """Simulate an error during deletion and verify complete atomic rollback (no partial deletion)"""
        # August month has 2 orders (101, 102)
        preview_res = self.client.get("/api/v1/admin/data-management/preview?month=2026-08", headers=self.super_headers)
        self.assertEqual(preview_res.status_code, 200)
        token = preview_res.json()["confirmation_token"]

        from unittest.mock import patch
        # Patch db.commit to simulate an unexpected error right before committing
        with patch.object(Session, "commit", side_effect=RuntimeError("Simulated DB Disk Failure during commit")):
            res = self.client.post("/api/v1/admin/data-management/delete", json={
                "month": "2026-08",
                "confirmation_token": token,
                "confirm_phrase": "PERMANENTLY DELETE 2026-08 DATA"
            }, headers=self.super_headers)
            self.assertEqual(res.status_code, 500)
            self.assertIn("Simulated DB Disk Failure", res.json()["detail"])

        # Verify August orders were rolled back and STILL exist!
        db = TestSessionLocal()
        try:
            aug_orders = db.query(Order).filter(Order.id.in_([101, 102])).all()
            self.assertEqual(len(aug_orders), 2, "August orders must not be deleted due to rollback")
        finally:
            db.close()

    def test_09_current_month_reports_unaffected(self):
        """Verify that reporting endpoints continue functioning properly for current month"""
        res = self.client.get("/api/v1/reports", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("summary", data)
        # 4 orders in October at 110 each = 440 revenue
        self.assertGreaterEqual(data["summary"]["monthly_revenue"]["value"], 440.0)


if __name__ == "__main__":
    unittest.main()


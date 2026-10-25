import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.main import app
from app.db import SessionLocal
from app.models.user import User
from app.models.order import Order, OrderItem
from app.models.menu import MenuItem
from app.models.restaurant import Restaurant
from app.models.table import Table
from app.utils.auth import create_token
from app.routes.orders import parse_business_timestamp

class OrderIdempotencyAndTimestampSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Ensure restaurant exists
        cls.restaurant = cls.db.query(Restaurant).filter(Restaurant.id == 1).first()
        if not cls.restaurant:
            cls.restaurant = Restaurant(id=1, name="Data Udipi Test Branch")
            cls.db.add(cls.restaurant)
            cls.db.commit()
            cls.db.refresh(cls.restaurant)

        # Ensure cashier user exists
        cls.cashier = cls.db.query(User).filter(User.role == "CASHIER", User.restaurant_id == 1).first()
        if not cls.cashier:
            cls.cashier = User(
                name="Test Cashier Idempotent",
                email="test_cashier_idempotent@dataudipi.com",
                password_hash="fakehash",
                role="CASHIER",
                restaurant_id=1,
                is_active=True
            )
            cls.db.add(cls.cashier)
            cls.db.commit()
            cls.db.refresh(cls.cashier)

        cls.token = create_token(cls.cashier)
        cls.headers = {
            "Authorization": f"Bearer {cls.token}",
            "Content-Type": "application/json"
        }

        # Ensure a menu item exists
        cls.menu_item = cls.db.query(MenuItem).filter(MenuItem.restaurant_id == 1).first()
        if not cls.menu_item:
            cls.menu_item = MenuItem(name="Idempotent Test Dosa", price=120.0, restaurant_id=1, is_available=True)
            cls.db.add(cls.menu_item)
            cls.db.commit()
            cls.db.refresh(cls.menu_item)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_parse_business_timestamp(self):
        """Test timestamp parsing for ISO strings, epoch ms/s, and formatted dates."""
        # ISO string with UTC Z
        dt = parse_business_timestamp("2026-10-20T14:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 20)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)

        # ISO string with offset (+05:30)
        dt_offset = parse_business_timestamp("2026-10-20T20:00:00+05:30")
        self.assertIsNotNone(dt_offset)
        self.assertEqual(dt_offset.hour, 14) # 20:00 - 5:30 = 14:30 UTC
        self.assertEqual(dt_offset.minute, 30)

        # Epoch milliseconds
        epoch_ms = 1729853400000
        dt_epoch = parse_business_timestamp(epoch_ms)
        self.assertIsNotNone(dt_epoch)

        # Epoch milliseconds as string
        dt_epoch_str = parse_business_timestamp("1729853400000")
        self.assertIsNotNone(dt_epoch_str)
        self.assertEqual(dt_epoch, dt_epoch_str)

        # Formatted string
        dt_fmt = parse_business_timestamp("2026-10-20 14:30:00")
        self.assertIsNotNone(dt_fmt)
        self.assertEqual(dt_fmt.hour, 14)

        # Empty / None
        self.assertIsNone(parse_business_timestamp(None))
        self.assertIsNone(parse_business_timestamp(""))

    def test_02_idempotency_via_header(self):
        """POST /api/v1/orders with Idempotency-Key header returns 200 on retry and does NOT duplicate."""
        idem_key = f"key-header-{uuid.uuid4()}"
        headers = dict(self.headers)
        headers["Idempotency-Key"] = idem_key

        payload = {
            "table_number": "T-10",
            "order_type": "DINE_IN",
            "payment_method": "Cash",
            "cart": [{"id": self.menu_item.id, "quantity": 2, "price": self.menu_item.price}],
            "total_amount": 240.0,
            "customer_phone": "9998887771"
        }

        # 1. First request
        res1 = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=headers)
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertIn("orderId", data1)
        order_id1 = data1["orderId"]
        bill_no1 = data1.get("bill_no")

        # Verify order in DB
        db = SessionLocal()
        order_db1 = db.query(Order).filter(Order.id == order_id1).first()
        self.assertIsNotNone(order_db1)
        self.assertEqual(order_db1.idempotency_key, idem_key)
        self.assertEqual(len(order_db1.items), 1)

        # Count total orders with this key
        count1 = db.query(Order).filter(Order.idempotency_key == idem_key).count()
        self.assertEqual(count1, 1)

        # 2. Second request with same Idempotency-Key (simulating retry after dropped response)
        res2 = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=headers)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()

        # MUST return existing order info
        self.assertEqual(data2["orderId"], order_id1)
        self.assertEqual(data2.get("bill_no"), bill_no1)
        self.assertTrue(data2.get("idempotent"))
        self.assertTrue(data2.get("is_duplicate"))

        # Verify NO second row created in DB
        count2 = db.query(Order).filter(Order.idempotency_key == idem_key).count()
        self.assertEqual(count2, 1)

        # Verify items were NOT duplicated
        db.refresh(order_db1)
        self.assertEqual(len(order_db1.items), 1)
        db.close()

    def test_03_idempotency_via_client_order_id_in_body(self):
        """POST /api/v1/orders with client_order_id in body prevents duplicate rows."""
        client_order_id = f"client-ord-{uuid.uuid4()}"

        payload = {
            "client_order_id": client_order_id,
            "table_number": "takeaway",
            "order_type": "TAKEAWAY",
            "payment_method": "UPI",
            "cart": [{"id": self.menu_item.id, "quantity": 1, "price": self.menu_item.price}],
            "total_amount": 120.0,
            "customer_phone": "9998887772"
        }

        # 1. First send
        res1 = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=self.headers)
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        order_id = data1["orderId"]

        # 2. Second send with identical client_order_id
        res2 = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=self.headers)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()

        self.assertEqual(data2["orderId"], order_id)
        self.assertTrue(data2.get("idempotent"))

        # Check DB count
        db = SessionLocal()
        count = db.query(Order).filter(Order.client_order_id == client_order_id).count()
        self.assertEqual(count, 1)
        db.close()

    def test_04_business_timestamp_outage_reporting(self):
        """Honour X-Original-Timestamp and body timestamp as the business time of the sale."""
        # Outage sale: Entered 3 days ago during internet failure
        past_business_time = (datetime.utcnow() - timedelta(days=3)).replace(microsecond=0)
        past_iso = past_business_time.isoformat() + "Z"

        idem_key = f"key-outage-{uuid.uuid4()}"
        headers = dict(self.headers)
        headers["Idempotency-Key"] = idem_key
        headers["X-Original-Timestamp"] = past_iso

        payload = {
            "client_order_id": idem_key,
            "table_number": "takeaway",
            "order_type": "TAKEAWAY",
            "payment_method": "Cash",
            "cart": [{"id": self.menu_item.id, "quantity": 1, "price": 50.0}],
            "total_amount": 50.0
        }

        res = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=headers)
        self.assertEqual(res.status_code, 200)
        order_id = res.json()["orderId"]

        db = SessionLocal()
        order_db = db.query(Order).filter(Order.id == order_id).first()
        self.assertIsNotNone(order_db)

        # Verify created_at matches the business time (3 days ago), NOT current time
        diff_seconds = abs((order_db.created_at - past_business_time).total_seconds())
        self.assertLess(diff_seconds, 2, "created_at must match the original outage business timestamp")
        db.close()

    def test_05_body_timestamp_support(self):
        """timestamp in request body is honoured as created_at business time."""
        past_time = (datetime.utcnow() - timedelta(days=5)).replace(microsecond=0)
        idem_key = f"key-body-ts-{uuid.uuid4()}"

        payload = {
            "client_order_id": idem_key,
            "timestamp": past_time.isoformat() + "Z",
            "table_number": "takeaway",
            "order_type": "TAKEAWAY",
            "payment_method": "Cash",
            "cart": [{"id": self.menu_item.id, "quantity": 1, "price": 60.0}],
            "total_amount": 60.0
        }

        res = self.client.post("/api/v1/orders?restaurant_id=1", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        order_id = res.json()["orderId"]

        db = SessionLocal()
        order_db = db.query(Order).filter(Order.id == order_id).first()
        self.assertIsNotNone(order_db)
        diff_seconds = abs((order_db.created_at - past_time).total_seconds())
        self.assertLess(diff_seconds, 2)
        db.close()

    def test_06_database_unique_index_enforcement(self):
        """Database level unique index prevents duplicate rows even on raw insert."""
        key = f"unique-raw-{uuid.uuid4()}"
        db = SessionLocal()

        order1 = Order(
            restaurant_id=1,
            table_id=None,
            order_source="CASHIER_POS",
            total_amount=100.0,
            status="PENDING",
            idempotency_key=key,
            client_order_id=key
        )
        db.add(order1)
        db.commit()

        # Second order with identical key MUST raise IntegrityError
        order2 = Order(
            restaurant_id=1,
            table_id=None,
            order_source="CASHIER_POS",
            total_amount=100.0,
            status="PENDING",
            idempotency_key=key,
            client_order_id=key
        )
        db.add(order2)
        with self.assertRaises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()

if __name__ == "__main__":
    unittest.main()

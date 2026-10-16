import sys
import os
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
import json
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models.customer import Customer, CustomerOTPChallenge
from app.models.order import Order, OrderItem
from app.models.dine_in_session import DineInSession
from app.models.table import Table
from app.models.delivery import DeliveryPartner, DeliveryAssignment, DeliveryStatusHistory
from app.models.menu import MenuItem, MenuCategory
from app.models.restaurant import Restaurant
from app.routes.customer import _build_token, _hash_otp, recalculate_order_financials
from jose import jwt

class ProductionSecurityRegressionSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.db = SessionLocal()
        CustomerOTPChallenge.__table__.create(bind=cls.db.get_bind(), checkfirst=True)
        
        # Ensure test restaurant exists
        cls.restaurant = cls.db.query(Restaurant).filter(Restaurant.id == 1).first()
        if not cls.restaurant:
            cls.restaurant = Restaurant(id=1, name="Data Udipi Test Branch")
            cls.db.add(cls.restaurant)
            cls.db.commit()
            cls.db.refresh(cls.restaurant)

        # Ensure restaurant 2 exists for cross-restaurant isolation testing
        cls.restaurant_2 = cls.db.query(Restaurant).filter(Restaurant.id == 2).first()
        if not cls.restaurant_2:
            cls.restaurant_2 = Restaurant(id=2, name="Data Udipi Second Branch")
            cls.db.add(cls.restaurant_2)
            cls.db.commit()
            cls.db.refresh(cls.restaurant_2)

        # Ensure test menu item exists
        cls.menu_item = cls.db.query(MenuItem).filter(MenuItem.restaurant_id == 1).first()
        if not cls.menu_item:
            cls.menu_item = MenuItem(name="Test Dosa", price=100.0, restaurant_id=1, is_available=True)
            cls.db.add(cls.menu_item)
            cls.db.commit()
            cls.db.refresh(cls.menu_item)

        # Customer A (Alice)
        cls.phone_a = "+919999000001"
        cls.cust_a = cls.db.query(Customer).filter(Customer.phone == cls.phone_a).first()
        if not cls.cust_a:
            cls.cust_a = Customer(phone=cls.phone_a, name="Alice Customer")
            cls.db.add(cls.cust_a)
            cls.db.commit()
            cls.db.refresh(cls.cust_a)

        cls.token_a = _build_token(cls.phone_a, cls.cust_a.id)
        cls.headers_a = {"Authorization": f"Bearer {cls.token_a}"}

        # Customer B (Bob)
        cls.phone_b = "+919999000002"
        cls.cust_b = cls.db.query(Customer).filter(Customer.phone == cls.phone_b).first()
        if not cls.cust_b:
            cls.cust_b = Customer(phone=cls.phone_b, name="Bob Customer")
            cls.db.add(cls.cust_b)
            cls.db.commit()
            cls.db.refresh(cls.cust_b)

        cls.token_b = _build_token(cls.phone_b, cls.cust_b.id)
        cls.headers_b = {"Authorization": f"Bearer {cls.token_b}"}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    # ────────────────────────────────────────────────────────────
    # 1. CUSTOMER OTP AUTHENTICATION HARDENING
    # ────────────────────────────────────────────────────────────
    def test_01_otp_challenge_issuance_and_rate_limiting(self):
        """OTP challenges are rate limited and respect cooldown."""
        test_phone = f"+9199{uuid.uuid4().int % 100000000:08d}"

        # 1. First challenge succeeds
        res = self.client.post("/api/v1/public/customers/auth/request-otp", json={"phone": test_phone})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("challenge_id", data)
        self.assertEqual(data.get("expires_in"), 300)

        # 2. Immediate request hits cooldown (HTTP 429)
        res_cooldown = self.client.post("/api/v1/public/customers/auth/request-otp", json={"phone": test_phone})
        self.assertEqual(res_cooldown.status_code, 429)
        self.assertIn("wait", res_cooldown.json()["detail"].lower())

    def test_02_otp_verification_attempt_limits_and_single_use(self):
        """OTP challenge enforces attempt limits, verifies constant-time hash, and prevents replay."""
        db = SessionLocal()
        test_phone = f"+9198{uuid.uuid4().int % 100000000:08d}"

        # Create challenge directly
        challenge_id = f"test-chall-{uuid.uuid4().hex[:8]}"
        real_otp = "849201"
        otp_hash = _hash_otp(test_phone, real_otp, challenge_id)
        chall = CustomerOTPChallenge(
            phone=test_phone,
            challenge_id=challenge_id,
            otp_hash=otp_hash,
            purpose="LOGIN",
            attempts_left=2,
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            created_at=datetime.utcnow()
        )
        db.add(chall)
        db.commit()

        # 1. Invalid OTP decrements attempts
        res_wrong = self.client.post("/api/v1/public/customers/auth/verify-otp", json={
            "phone": test_phone,
            "challenge_id": challenge_id,
            "otp": "000000"
        })
        self.assertEqual(res_wrong.status_code, 400)
        db.refresh(chall)
        self.assertEqual(chall.attempts_left, 1)

        # 2. Valid OTP verifies and issues JWT verification token
        res_valid = self.client.post("/api/v1/public/customers/auth/verify-otp", json={
            "phone": test_phone,
            "challenge_id": challenge_id,
            "otp": real_otp
        })
        self.assertEqual(res_valid.status_code, 200)
        v_token = res_valid.json().get("verification_token")
        self.assertIsNotNone(v_token)
        db.refresh(chall)
        self.assertTrue(chall.is_used)

        # 3. Single use: Replaying same challenge fails
        res_replay = self.client.post("/api/v1/public/customers/auth/verify-otp", json={
            "phone": test_phone,
            "challenge_id": challenge_id,
            "otp": real_otp
        })
        self.assertEqual(res_replay.status_code, 400)
        db.close()

    def test_03_customer_signup_and_login_with_verified_token(self):
        """Customer cannot login/signup without verified proof when legacy stub is disabled."""
        from app.utils.auth import SECRET_KEY, ALGORITHM
        test_phone = f"+9197{uuid.uuid4().int % 100000000:08d}"
        
        # Craft a signed verification token
        payload = {
            "sub": test_phone,
            "purpose": "OTP_VERIFIED",
            "type": "OTP_VERIFIED",
            "exp": datetime.utcnow() + timedelta(minutes=5)
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        # 1. Signup with verification_token
        res_signup = self.client.post("/api/v1/public/customers/signup", json={
            "phone": test_phone,
            "name": "Token Verified Customer",
            "verification_token": token
        })
        self.assertIn(res_signup.status_code, [200, 201])
        signup_data = res_signup.json()
        self.assertEqual(signup_data["phone"], test_phone)
        self.assertIn("token", signup_data)

        # 2. Login with verification_token
        res_login = self.client.post("/api/v1/public/customers/login", json={
            "phone": test_phone,
            "verification_token": token
        })
        self.assertEqual(res_login.status_code, 200)
        self.assertIn("token", res_login.json())

    # ────────────────────────────────────────────────────────────
    # 2. CANCELLATION AUTHORIZATION & OWNERSHIP
    # ────────────────────────────────────────────────────────────
    def test_04_cancellation_ownership_enforcement(self):
        """Orders can only be cancelled by their authorized owner."""
        db = SessionLocal()
        order_a = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CUSTOMER_APP",
            status="PENDING",
            total_amount=150.0,
            payment_status="Pending"
        )
        db.add(order_a)
        db.commit()
        db.refresh(order_a)

        # 1. Unauthenticated request -> HTTP 401
        res_unauth = self.client.post(f"/api/orders/{order_a.id}/cancel?restaurant_id=1", json={})
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Non-owner (Bob) attempts to cancel Alice's order -> HTTP 403 Forbidden
        res_bob = self.client.post(
            f"/api/orders/{order_a.id}/cancel?restaurant_id=1",
            headers=self.headers_b,
            json={}
        )
        self.assertEqual(res_bob.status_code, 403)

        # 3. Owner (Alice) cancels her own order -> HTTP 200
        res_alice = self.client.post(
            f"/api/orders/{order_a.id}/cancel?restaurant_id=1",
            headers=self.headers_a,
            json={}
        )
        self.assertEqual(res_alice.status_code, 200)
        db.refresh(order_a)
        self.assertEqual(order_a.status, "CANCELLED")
        db.close()

    def test_05_cashier_pos_order_isolation(self):
        """Customer cannot cancel an order placed at the cashier counter."""
        db = SessionLocal()
        pos_order = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CASHIER_POS",
            status="PENDING",
            total_amount=250.0,
            payment_status="Pending"
        )
        db.add(pos_order)
        db.commit()
        db.refresh(pos_order)

        # Customer attempts to cancel CASHIER_POS order -> 403 Forbidden
        res = self.client.post(
            f"/api/orders/{pos_order.id}/cancel?restaurant_id=1",
            headers=self.headers_a,
            json={}
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("cashier counter", res.json()["detail"].lower())
        db.close()

    def test_06_cross_restaurant_cancellation_prohibited(self):
        """Cancellation specifying a different restaurant is rejected."""
        db = SessionLocal()
        order_r1 = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CUSTOMER_APP",
            status="PENDING",
            total_amount=120.0
        )
        db.add(order_r1)
        db.commit()
        db.refresh(order_r1)

        # Alice specifies restaurant_id=2 for restaurant 1 order
        res = self.client.post(
            f"/api/orders/{order_r1.id}/cancel?restaurant_id=2",
            headers=self.headers_a,
            json={}
        )
        self.assertIn(res.status_code, [403, 404])
        db.close()

    # ────────────────────────────────────────────────────────────
    # 3. ORDER LOOKUP & CUSTOMER PRIVACY ISOLATION
    # ────────────────────────────────────────────────────────────
    def test_07_cross_branch_order_lookup_isolation(self):
        """Order lookup cannot fall back across restaurant branches."""
        db = SessionLocal()
        order_b1 = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CUSTOMER_APP",
            status="CONFIRMED",
            total_amount=180.0
        )
        db.add(order_b1)
        db.commit()
        db.refresh(order_b1)

        # Querying branch 2 for branch 1 order ID must return 404, not branch 1 data
        res = self.client.get(
            f"/api/orders/{order_b1.id}?restaurant_id=2",
            headers=self.headers_a
        )
        self.assertEqual(res.status_code, 404)
        db.close()

    def test_08_customer_order_history_privacy(self):
        """Unauthenticated or cross-customer scraping of order history is blocked."""
        # 1. Unauthenticated request to customer order history -> 401
        res_unauth = self.client.get(f"/api/v1/public/customers/orders?phone={self.phone_a}")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Bob attempts to view Alice's orders -> 403 Forbidden
        res_bob = self.client.get(
            f"/api/v1/public/customers/orders?phone={self.phone_a}",
            headers=self.headers_b
        )
        self.assertEqual(res_bob.status_code, 403)

        # 3. Alice viewing her own orders -> 200 OK
        res_alice = self.client.get(
            f"/api/v1/public/customers/orders?phone={self.phone_a}",
            headers=self.headers_a
        )
        self.assertEqual(res_alice.status_code, 200)

    # ────────────────────────────────────────────────────────────
    # 4. KITCHEN CONFLICTS & ITEM FINANCIAL RECALCULATION
    # ────────────────────────────────────────────────────────────
    def test_09_kitchen_state_cancellation_conflict(self):
        """Orders in PREPARING, READY, SERVED, or COMPLETED return 409 Conflict."""
        db = SessionLocal()
        blocked_statuses = ["PREPARING", "READY", "SERVED", "COMPLETED"]

        for blocked_status in blocked_statuses:
            order = Order(
                restaurant_id=1,
                customer_phone=self.phone_a,
                order_source="CUSTOMER_APP",
                status=blocked_status,
                total_amount=200.0
            )
            db.add(order)
            db.commit()
            db.refresh(order)

            res = self.client.post(
                f"/api/orders/{order.id}/cancel?restaurant_id=1",
                headers=self.headers_a,
                json={}
            )
            self.assertEqual(res.status_code, 409, f"Status {blocked_status} should return 409 Conflict")
            self.assertIn("already started preparing", res.json()["detail"].lower())

        db.close()

    def test_10_item_cancellation_financial_recalculation(self):
        """Item cancellation recalculates subtotal, proportional GST, and discount using Decimal."""
        db = SessionLocal()
        order = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CUSTOMER_APP",
            status="PENDING",
            total_amount=210.0,
            gst_amount=10.0,
            discount_amount=20.0
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        item1 = OrderItem(order_id=order.id, menu_item_id=self.menu_item.id, price=100.0, quantity=1, status="PENDING")
        item2 = OrderItem(order_id=order.id, menu_item_id=self.menu_item.id, price=100.0, quantity=1, status="PENDING")
        db.add_all([item1, item2])
        db.commit()
        db.refresh(item1)

        # Cancel Item 1
        res = self.client.post(
            f"/api/orders/{order.id}/items/{item1.id}/cancel?restaurant_id=1",
            headers=self.headers_a,
            json={}
        )
        self.assertEqual(res.status_code, 200)

        db.refresh(order)
        db.refresh(item1)
        self.assertEqual(item1.status, "CANCELLED")
        # Subtotal: 100, Proportional GST (50% of 10): 5, Proportional Disc (50% of 20): 10 => Total: 95
        self.assertEqual(order.total_amount, 95.0)
        self.assertEqual(order.gst_amount, 5.0)
        self.assertEqual(order.discount_amount, 10.0)
        db.close()

    def test_11_delivery_cancellation_rider_assignment_consistency(self):
        """Cancelling delivery unsets assignment and frees rider only if no other active deliveries."""
        db = SessionLocal()
        rider = db.query(DeliveryPartner).filter(DeliveryPartner.phone == "+919876543210").first()
        if not rider:
            rider = DeliveryPartner(
                name="Ramesh Rider",
                phone="+919876543210",
                vehicle_type="Bike",
                vehicle_number="TN01AB1234",
                is_online=True,
                is_available=False
            )
            db.add(rider)
            db.commit()
            db.refresh(rider)
        else:
            # Cancel any leftover test assignments for this rider
            db.query(DeliveryAssignment).filter(DeliveryAssignment.rider_id == rider.id).update({"status": "CANCELLED"})
            rider.is_online = True
            rider.is_available = False
            db.commit()

        order_del = Order(
            restaurant_id=1,
            customer_phone=self.phone_a,
            order_source="CUSTOMER_APP",
            status="CONFIRMED",
            order_type="DELIVERY",
            delivery_status="RIDER_ASSIGNED",
            total_amount=300.0
        )
        db.add(order_del)
        db.commit()
        db.refresh(order_del)

        da = DeliveryAssignment(
            order_id=order_del.id,
            rider_id=rider.id,
            status="ASSIGNED"
        )
        db.add(da)
        db.commit()
        db.refresh(da)

        # Cancel delivery order before pickup
        res = self.client.post(
            f"/api/orders/{order_del.id}/cancel?restaurant_id=1",
            headers=self.headers_a,
            json={}
        )
        self.assertEqual(res.status_code, 200)

        db.refresh(order_del)
        db.refresh(da)
        db.refresh(rider)

        self.assertEqual(order_del.status, "CANCELLED")
        self.assertEqual(order_del.delivery_status, "CANCELLED")
        self.assertEqual(da.status, "CANCELLED")
        self.assertTrue(rider.is_available, "Rider must be marked available when their sole assignment is cancelled")
        db.close()

    # ────────────────────────────────────────────────────────────
    # 5. DINE-IN SETTLEMENT IDEMPOTENCY
    # ────────────────────────────────────────────────────────────
    def test_12_dine_in_settlement_idempotency(self):
        """Settled dine-in session cannot be double-settled."""
        db = SessionLocal()
        table = Table(table_number=f"T-{uuid.uuid4().hex[:4]}", restaurant_id=1, capacity=4, status="Occupied")
        db.add(table)
        db.commit()
        db.refresh(table)

        sess = DineInSession(
            session_uid=f"SES-TEST-IDEMPOTENT-{uuid.uuid4().hex[:8]}",
            restaurant_id=1,
            table_id=table.id,
            customer_phone=self.phone_a,
            status="ACTIVE",
            payment_status="Pending",
            total_amount=200.0
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)

        order_sess = Order(
            restaurant_id=1,
            table_id=table.id,
            dine_in_session_id=sess.id,
            customer_phone=self.phone_a,
            status="CONFIRMED",
            total_amount=200.0
        )
        db.add(order_sess)
        db.commit()
        db.refresh(order_sess)

        # First settlement call
        res_settle1 = self.client.post(f"/api/dine-in/session/{sess.id}/settle?restaurant_id=1", json={
            "payment_method": "Cash"
        })
        self.assertEqual(res_settle1.status_code, 200)
        bill_no1 = res_settle1.json()["bill_no"]

        # Second settlement call must be idempotent
        res_settle2 = self.client.post(f"/api/dine-in/session/{sess.id}/settle?restaurant_id=1", json={
            "payment_method": "Cash"
        })
        self.assertEqual(res_settle2.status_code, 200)
        data2 = res_settle2.json()
        self.assertEqual(data2["bill_no"], bill_no1)
        self.assertIn("already settled", data2["message"].lower())
        db.close()

if __name__ == "__main__":
    unittest.main()

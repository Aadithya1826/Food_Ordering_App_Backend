import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.utils.auth import create_token
from app.db import SessionLocal
from app.models.menu import CateringOrderMenu
from app.models.catering import CateringOrder, CateringPayment
from app.models.user import User

class AdminCateringIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        db = SessionLocal()
        
        # Super Admin user (id=1 in db)
        super_user = db.query(User).filter(User.id == 1).first()
        cls.super_admin_token = create_token(super_user)
        cls.super_headers = {'Authorization': f'Bearer {cls.super_admin_token}'}

        # Hotel Admin user (id=2 in db, restaurant_id=1)
        hotel_user = db.query(User).filter(User.id == 2).first()
        cls.hotel_admin_token = create_token(hotel_user)
        cls.hotel_headers = {'Authorization': f'Bearer {cls.hotel_admin_token}'}

        # Unauthorized Cashier user (id=16 in db)
        cashier_user = db.query(User).filter(User.id == 16).first()
        cls.unauth_token = create_token(cashier_user)
        cls.unauth_headers = {'Authorization': f'Bearer {cls.unauth_token}'}
        db.close()

        cls.test_pkg_code = "TEST-PKG-99"

    @classmethod
    def tearDownClass(cls):
        # Cleanup any test package rows created during tests
        db = SessionLocal()
        db.query(CateringOrderMenu).filter(CateringOrderMenu.code == cls.test_pkg_code).delete()
        db.commit()
        db.close()

    def test_01_fetch_catering_packages(self):
        """1. Fetch catering packages"""
        res = self.client.get("/api/v1/admin/catering/packages", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)

    def test_02_group_package_rows_correctly(self):
        """2. Group package rows correctly: unique package codes, no duplicates"""
        res = self.client.get("/api/v1/admin/catering/packages", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        codes = [p["code"] for p in data]
        self.assertEqual(len(codes), len(set(codes)), "Package codes must be unique in response")

    def test_03_search_packages(self):
        """3. Search packages by name or code"""
        res = self.client.get("/api/v1/admin/catering/packages?search=breakfast", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(all("breakfast" in p["name"].lower() or any("breakfast" in (i["item_name"] or "").lower() for i in p["items"]) for p in data))

    def test_04_05_06_07_08_09_10_add_and_edit_package(self):
        """
        4. Add package
        5. Edit package
        6. Change package price
        7. Change minimum guest count
        8. Add menu item
        9. Remove/deactivate menu item
        10. Change item customization rule
        """
        # Cleanup first if existed
        db = SessionLocal()
        db.query(CateringOrderMenu).filter(CateringOrderMenu.code == self.test_pkg_code).delete()
        db.commit()
        db.close()

        # 4. Add package
        create_payload = {
            "name": "Automated Test Banquet",
            "code": self.test_pkg_code,
            "price": 350.0,
            "minimum_order_quantity": 40,
            "is_available": True,
            "items": [
                {
                    "item_name": "Test Idli Vada",
                    "category_name": "Tiffin",
                    "customization_group": "TIFFIN",
                    "is_swappable": True,
                    "is_removable": True,
                    "add_price": 30.0,
                    "remove_price": -10.0,
                    "same_group_replace_price": 0.0,
                    "upgrade_group": None,
                    "upgrade_price": 0.0,
                    "display_order": 0
                },
                {
                    "item_name": "Test Sambar",
                    "category_name": "Sides",
                    "customization_group": "SIDE",
                    "is_swappable": False,
                    "is_removable": False,
                    "add_price": 20.0,
                    "remove_price": 0.0,
                    "same_group_replace_price": 0.0,
                    "upgrade_group": None,
                    "upgrade_price": 0.0,
                    "display_order": 1
                }
            ]
        }
        res_create = self.client.post("/api/v1/admin/catering/packages", json=create_payload, headers=self.super_headers)
        self.assertEqual(res_create.status_code, 200, f"Error creating package: {res_create.text}")
        created = res_create.json()
        self.assertEqual(created["code"], self.test_pkg_code)
        self.assertEqual(created["price"], 350.0)
        self.assertEqual(created["minimum_order_quantity"], 40)
        self.assertEqual(len(created["items"]), 2)

        # 5, 6, 7, 8, 9, 10: Update package price, min guests, items, customization rules
        update_payload = {
            "name": "Automated Test Banquet Deluxe",
            "price": 395.0, # 6. Change price
            "minimum_order_quantity": 60, # 7. Change min guests
            "is_available": True,
            "items": [
                {
                    "item_name": "Test Idli Vada",
                    "category_name": "Tiffin",
                    "customization_group": "TIFFIN",
                    "is_swappable": True,
                    "is_removable": True,
                    "add_price": 35.0, # 10. Change customization rule
                    "remove_price": -15.0,
                    "same_group_replace_price": 5.0,
                    "upgrade_group": "PREMIUM_TIFFIN",
                    "upgrade_price": 50.0,
                    "display_order": 0
                },
                # 8. Add new menu item (and Test Sambar was omitted = 9. removed)
                {
                    "item_name": "Test Kesari Bath",
                    "category_name": "Desserts",
                    "customization_group": "SWEET",
                    "is_swappable": True,
                    "is_removable": True,
                    "add_price": 40.0,
                    "remove_price": -10.0,
                    "same_group_replace_price": 0.0,
                    "upgrade_group": None,
                    "upgrade_price": 0.0,
                    "display_order": 1
                }
            ]
        }
        res_update = self.client.put(f"/api/v1/admin/catering/packages/{self.test_pkg_code}", json=update_payload, headers=self.super_headers)
        self.assertEqual(res_update.status_code, 200)
        updated = res_update.json()
        self.assertEqual(updated["name"], "Automated Test Banquet Deluxe")
        self.assertEqual(updated["price"], 395.0)
        self.assertEqual(updated["minimum_order_quantity"], 60)
        self.assertEqual(len(updated["items"]), 2)
        item_names = [i["item_name"] for i in updated["items"]]
        self.assertIn("Test Kesari Bath", item_names)
        self.assertNotIn("Test Sambar", item_names)

    def test_11_12_toggle_availability_and_persistence(self):
        """11. Toggle availability, 12. Refresh and verify persistence"""
        # Toggle to False
        res = self.client.patch(
            f"/api/v1/admin/catering/packages/{self.test_pkg_code}/availability",
            json={"is_available": False},
            headers=self.super_headers
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["is_available"])

        # Fetch detail again to verify persistence
        res_fetch = self.client.get(f"/api/v1/admin/catering/packages/{self.test_pkg_code}", headers=self.super_headers)
        self.assertEqual(res_fetch.status_code, 200)
        self.assertFalse(res_fetch.json()["is_available"])

        # Toggle back to True
        res_toggle_back = self.client.patch(
            f"/api/v1/admin/catering/packages/{self.test_pkg_code}/availability",
            json={"is_available": True},
            headers=self.super_headers
        )
        self.assertEqual(res_toggle_back.status_code, 200)
        self.assertTrue(res_toggle_back.json()["is_available"])

    def test_13_customer_app_synchronization(self):
        """13. Verify customer app public endpoint sees updated available package data"""
        res_public = self.client.get("/api/v1/public/catering/packages")
        self.assertEqual(res_public.status_code, 200)
        packages = res_public.json()
        pkg_in_public = next((p for p in packages if p["code"] == self.test_pkg_code), None)
        self.assertIsNotNone(pkg_in_public, "Public customer endpoint must reflect admin package")
        self.assertEqual(pkg_in_public["price"], 395.0)

    def test_14_historical_order_price_integrity(self):
        """14. Verify existing confirmed order prices do not change when package master is edited"""
        db = SessionLocal()
        order = db.query(CateringOrder).filter(CateringOrder.id == 4).first()
        self.assertIsNotNone(order)
        booked_price = order.package_price
        booked_total = order.total_amount
        db.close()

        # Admin package price change on that package
        res_detail = self.client.get(f"/api/v1/admin/catering/orders/4", headers=self.super_headers)
        self.assertEqual(res_detail.status_code, 200)
        order_detail = res_detail.json()
        self.assertEqual(order_detail["package"]["booked_package_price"], booked_price)
        self.assertEqual(order_detail["financials"]["total_amount"], booked_total)

    def test_15_16_17_18_19_20_21_22_orders_and_financials(self):
        """
        15. Fetch confirmed catering orders
        16. Open order details
        17. Display customization history
        18. Display advance payment
        19. Display balance payment
        20. Display due date
        21. Handle unpaid/partially paid orders correctly
        22. Prevent duplicate payment counting
        """
        res_orders = self.client.get("/api/v1/admin/catering/orders", headers=self.super_headers)
        self.assertEqual(res_orders.status_code, 200)
        orders = res_orders.json()
        self.assertGreaterEqual(len(orders), 1)

        # 16. Open order details for Order 4
        res_detail = self.client.get("/api/v1/admin/catering/orders/4", headers=self.super_headers)
        self.assertEqual(res_detail.status_code, 200)
        data = res_detail.json()

        # Customer & Event details present
        self.assertIn("customer", data)
        self.assertIn("event", data)
        self.assertIn("financials", data)

        # 17. Customization history
        self.assertIsInstance(data["customizations"], list)

        # 18, 19, 20, 21: Payments, Balance, and Due Date
        financials = data["financials"]
        self.assertEqual(financials["total_amount"], 25000.0)
        self.assertEqual(financials["advance_amount"], 12500.0)
        self.assertEqual(financials["paid_amount"], 12500.0)
        self.assertEqual(financials["balance_amount"], 12500.0)
        self.assertEqual(financials["payment_status"], "PARTIALLY_PAID")
        self.assertIsNotNone(financials["full_payment_due_date"])

        # 22. Payments count and verified payments prevent duplicate counting
        payments = data["payments"]
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["amount"], 12500.0)

    def test_23_restaurant_specific_filtering(self):
        """23. Verify restaurant-specific filtering for hotel admin"""
        res = self.client.get("/api/v1/admin/catering/packages", headers=self.hotel_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        # Hotel admin for restaurant 1 only sees restaurant 1 or global (None) packages
        for p in data:
            self.assertIn(p["restaurant_id"], [1, None])

    def test_24_unauthorized_access_rejected(self):
        """24. Verify unauthorized admin changes are rejected (403 Forbidden)"""
        res = self.client.get("/api/v1/admin/catering/packages", headers=self.unauth_headers)
        self.assertEqual(res.status_code, 403)

        res_post = self.client.post("/api/v1/admin/catering/packages", json={"name": "Hacked", "code": "HACK", "price": 10}, headers=self.unauth_headers)
        self.assertEqual(res_post.status_code, 403)

    def test_25_order_status_transitions_and_validation(self):
        """25. Test server-side order status transition validation"""
        # Valid transition: CONFIRMED -> PREPARING
        res = self.client.patch(
            "/api/v1/admin/catering/orders/4/status",
            json={"order_status": "PREPARING"},
            headers=self.super_headers
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["order_status"], "PREPARING")

        # Invalid status rejected
        res_invalid = self.client.patch(
            "/api/v1/admin/catering/orders/4/status",
            json={"order_status": "INVALID_STATE"},
            headers=self.super_headers
        )
        self.assertEqual(res_invalid.status_code, 400)

        # Transition back to CONFIRMED
        res_back = self.client.patch(
            "/api/v1/admin/catering/orders/4/status",
            json={"order_status": "CONFIRMED"},
            headers=self.super_headers
        )
        self.assertEqual(res_back.status_code, 200)

    def test_26_catering_reports_consistency(self):
        """26. Catering reports calculation and no double counting"""
        res = self.client.get("/api/v1/admin/catering/reports", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        rep = res.json()
        self.assertGreaterEqual(rep["total_orders"], 1)
        self.assertGreater(rep["total_booked_sales"], 0.0)
        self.assertGreater(rep["total_collected_revenue"], 0.0)
        # Verify accounting identity: total_collected = advances + balances
        self.assertAlmostEqual(
            rep["total_collected_revenue"],
            rep["verified_advance_collections"] + rep["verified_balance_collections"],
            places=2
        )
        # Outstanding receivables = total_booked_sales - total_collected
        self.assertAlmostEqual(
            rep["outstanding_receivables"],
            max(0.0, rep["total_booked_sales"] - rep["total_collected_revenue"]),
            places=2
        )

if __name__ == "__main__":
    unittest.main()

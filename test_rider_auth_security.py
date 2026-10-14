import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models.delivery import DeliveryPartner, DeliveryAssignment, RiderSession
from app.models.order import Order
from app.utils.auth import hash_password, create_rider_token
from jose import jwt
from app.utils.auth import SECRET_KEY, ALGORITHM
from datetime import datetime, timedelta
import uuid

client = TestClient(app)

def run_tests():
    print("\n--- Starting Rider Security & JWT Test Suite ---\n")
    s = SessionLocal()
    try:
        # Prepare test riders
        # Rider 1 (Active, phone: +919876543211, password: Rider@1234)
        r1 = s.query(DeliveryPartner).filter(DeliveryPartner.id == 1).first()
        if not r1:
            r1 = DeliveryPartner(id=1, name="Test Rider 1", phone="+919876543211", is_active=True)
            s.add(r1)
        r1.is_active = True
        r1.password_hash = hash_password("Rider@1234")

        # Rider 2 (Active, phone: +919876543210, password: Rider@3210)
        r2 = s.query(DeliveryPartner).filter(DeliveryPartner.id == 2).first()
        if not r2:
            r2 = DeliveryPartner(id=2, name="Test Rider 2", phone="+919876543210", is_active=True)
            s.add(r2)
        r2.is_active = True
        r2.password_hash = hash_password("Rider@3210")

        # Rider 3 (Inactive account)
        r3 = s.query(DeliveryPartner).filter(DeliveryPartner.id == 3).first()
        if not r3:
            r3 = DeliveryPartner(id=3, name="Inactive Rider", phone="987654210", is_active=False)
            s.add(r3)
        r3.is_active = False
        r3.password_hash = hash_password("Rider@4210")

        s.commit()
    finally:
        s.close()

    # 1. Correct credentials login
    print("Test 1: Correct rider credentials...")
    res = client.post("/api/v1/rider/auth/login", json={"phone": "+919876543211", "password": "Rider@1234"})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert "access_token" in data, "access_token missing"
    assert data["token_type"] == "bearer", "token_type mismatch"
    token = data["access_token"]
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "1", f"sub mismatch: {payload['sub']}"
    assert payload["role"] == "RIDER", f"role mismatch: {payload['role']}"
    assert payload["type"] == "access", f"type mismatch: {payload['type']}"
    assert "jti" in payload, "jti missing"
    print("  -> Passed! Token claims valid.")

    # 2. Incorrect password
    print("Test 2: Incorrect password...")
    res = client.post("/api/v1/rider/auth/login", json={"phone": "+919876543211", "password": "WrongPassword!"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"
    print("  -> Passed! Rejected with 401.")

    # 3. Unknown rider
    print("Test 3: Unknown rider phone...")
    res = client.post("/api/v1/rider/auth/login", json={"phone": "9999999999", "password": "Rider@1234"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"
    print("  -> Passed! Rejected with 401.")

    # 4. Inactive rider
    print("Test 4: Inactive / disabled rider...")
    res = client.post("/api/v1/rider/auth/login", json={"phone": "987654210", "password": "Rider@4210"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"
    assert "disabled" in res.json().get("detail", "").lower(), "Expected disabled account message"
    print("  -> Passed! Disabled rider rejected with 401.")

    # 5. Missing JWT on protected endpoint
    print("Test 5: Missing JWT on protected endpoint...")
    res = client.get("/api/v1/riders/me")
    assert res.status_code == 401, f"Expected 401, got {res.status_code}"
    print("  -> Passed! 401 returned.")

    # 6. Expired JWT
    print("Test 6: Expired JWT...")
    expired_payload = {
        "sub": "1",
        "user_id": "1",
        "role": "RIDER",
        "account_type": "RIDER",
        "type": "access",
        "exp": datetime.utcnow() - timedelta(minutes=10),
        "iat": int((datetime.utcnow() - timedelta(minutes=70)).timestamp()),
        "jti": str(uuid.uuid4())
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)
    res = client.get("/api/v1/riders/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"
    print("  -> Passed! Expired token rejected.")

    # 7. Modified / Tampered JWT
    print("Test 7: Modified/tampered signature JWT...")
    tampered_token = token[:-5] + "XXXXX"
    res = client.get("/api/v1/riders/me", headers={"Authorization": f"Bearer {tampered_token}"})
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"
    print("  -> Passed! Tampered token rejected.")

    # 8. Wrong-role JWT (e.g. role = CUSTOMER or USER)
    print("Test 8: Wrong-role JWT...")
    wrong_role_payload = {
        "sub": "1",
        "user_id": "1",
        "role": "CUSTOMER",
        "account_type": "CUSTOMER",
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=60),
        "iat": int(datetime.utcnow().timestamp()),
        "jti": str(uuid.uuid4())
    }
    wrong_role_token = jwt.encode(wrong_role_payload, SECRET_KEY, algorithm=ALGORITHM)
    res = client.get("/api/v1/riders/me", headers={"Authorization": f"Bearer {wrong_role_token}"})
    assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
    print("  -> Passed! Wrong-role token rejected with 403.")

    # 9. Server-side session revocation / Logout
    print("Test 9: Server-side token revocation via logout...")
    # Verify token works first
    res = client.get("/api/v1/riders/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    
    # Call logout
    logout_res = client.post("/api/v1/rider/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_res.status_code == 200, f"Logout failed: {logout_res.text}"

    # Try accessing protected endpoint again with same token
    res_after_logout = client.get("/api/v1/riders/me", headers={"Authorization": f"Bearer {token}"})
    assert res_after_logout.status_code == 401, f"Expected 401 after logout, got {res_after_logout.status_code}: {res_after_logout.text}"
    assert "revoked" in res_after_logout.json().get("detail", "").lower(), "Expected revocation message"
    print("  -> Passed! Revoked token cannot access protected endpoints.")

    # 10. Rider A accesses Rider B's data
    print("Test 10: Cross-rider unauthorized access...")
    # Log in Rider 1 again
    r1_login = client.post("/api/v1/rider/auth/login", json={"phone": "+919876543211", "password": "Rider@1234"}).json()
    r1_token = r1_login["access_token"]

    # Try to access Rider 2's profile using Rider 1's token
    cross_res = client.get("/api/v1/delivery-partners/2", headers={"Authorization": f"Bearer {r1_token}"})
    assert cross_res.status_code == 403, f"Expected 403 for cross-rider profile access, got {cross_res.status_code}"

    # Try to modify Rider 2's online status using Rider 1's token
    cross_status_res = client.patch("/api/v1/delivery-partners/2/online-status", json={"is_online": False}, headers={"Authorization": f"Bearer {r1_token}"})
    assert cross_status_res.status_code == 403, f"Expected 403 for cross-rider status change, got {cross_status_res.status_code}"
    print("  -> Passed! Cross-rider tampering blocked with 403.")

    # 11. Rider A accesses Rider B's delivery assignment
    print("Test 11: Rider A accesses Rider B's delivery assignment...")
    db = SessionLocal()
    try:
        # Create a test assignment assigned to Rider 2 (ID 2)
        test_order = Order(order_type="DELIVERY", status="CONFIRMED", total_amount=250.0)
        db.add(test_order)
        db.commit()
        db.refresh(test_order)

        test_assignment = DeliveryAssignment(
            order_id=test_order.id,
            rider_id=2,
            status="ASSIGNED"
        )
        db.add(test_assignment)
        db.commit()
        db.refresh(test_assignment)
        assignment_id = test_assignment.id
        order_id = test_order.id
    finally:
        db.close()

    # Rider 1 attempts to accept Rider 2's assignment
    steal_res = client.post(f"/api/v1/delivery/assignments/{assignment_id}/accept", headers={"Authorization": f"Bearer {r1_token}"})
    assert steal_res.status_code == 403, f"Expected 403 when Rider 1 tries to accept Rider 2's assignment, got {steal_res.status_code}: {steal_res.text}"
    print("  -> Passed! Unauthorized assignment modification rejected with 403.")

    # 12. Concurrent / First-valid order acceptance
    print("Test 12: Order acceptance by assigned rider & rejecting second acceptance...")
    # Log in Rider 2
    r2_login = client.post("/api/v1/rider/auth/login", json={"phone": "+919876543210", "password": "Rider@3210"}).json()
    r2_token = r2_login["access_token"]

    # Rider 2 accepts the assignment
    r2_accept = client.post(f"/api/v1/delivery/assignments/{assignment_id}/accept", headers={"Authorization": f"Bearer {r2_token}"})
    assert r2_accept.status_code == 200, f"Expected 200 for assigned rider accept, got {r2_accept.status_code}: {r2_accept.text}"

    # Rider 1 tries to accept via available-orders endpoint after it was already accepted
    reaccept_res = client.post(f"/api/v1/rider/available-orders/{order_id}/accept", headers={"Authorization": f"Bearer {r1_token}"})
    assert reaccept_res.status_code == 404, f"Expected 404 for already accepted order, got {reaccept_res.status_code}"
    # 13. Registration with Name, Number, and SHA-256 password storage
    print("Test 13: Pre-authorized registration with Name, Number, and SHA-256 password...")
    import hashlib

    # 13a. Reject unauthorized random phone number
    unauth_res = client.post("/api/v1/rider/auth/register", json={
        "name": "Random Person",
        "phone": "+910000000000",
        "password": "Password@123"
    })
    assert unauth_res.status_code == 403, f"Expected 403 for unauthorized number, got {unauth_res.status_code}"
    print("  -> Passed! Unregistered number rejected with 403.")

    # 13b. Authorized rider sets up password
    reg_phone = "+919998887776"
    reg_password = "SecurePassword@123"
    reg_name = "Speedy Express Rider"

    s_clean = SessionLocal()
    existing_reg = s_clean.query(DeliveryPartner).filter(DeliveryPartner.phone.like("%9998887776%")).first()
    if not existing_reg:
        existing_reg = DeliveryPartner(name=reg_name, phone=reg_phone, is_active=True, password_hash=None)
        s_clean.add(existing_reg)
    else:
        existing_reg.password_hash = None
    s_clean.commit()
    s_clean.close()

    reg_res = client.post("/api/v1/rider/auth/register", json={
        "name": reg_name,
        "phone": reg_phone,
        "password": reg_password
    })
    assert reg_res.status_code == 200, f"Expected 200, got {reg_res.status_code}: {reg_res.text}"
    reg_data = reg_res.json()
    assert reg_data["rider"]["name"] == reg_name
    assert "access_token" in reg_data

    # Verify SHA-256 stored in DB
    s_verify = SessionLocal()
    saved_rider = s_verify.query(DeliveryPartner).filter(DeliveryPartner.phone.like("%9998887776%")).first()
    assert saved_rider is not None, "Rider not found in DB"
    expected_sha256 = hashlib.sha256(reg_password.encode('utf-8')).hexdigest()
    assert saved_rider.password_hash == expected_sha256, f"Expected SHA-256 hash {expected_sha256}, got {saved_rider.password_hash}"
    s_verify.close()
    print("  -> Passed! Authorized rider registered and password successfully stored using SHA-256 security.")

    print("\n[ALL 13 SECURITY TESTS PASSED SUCCESSFULLY!]\n")

if __name__ == "__main__":
    run_tests()



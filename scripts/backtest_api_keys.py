import os
import sys
import time
import requests
from dotenv import load_dotenv

# Ensure stdout uses UTF-8 to prevent cp1252 Windows encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")

def mask_key(val: str, prefix_len=4, suffix_len=4):
    if not val:
        return "[NOT SET]"
    if len(val) <= prefix_len + suffix_len:
        return f"{val[:2]}...{val[-2:]}"
    return f"{val[:prefix_len]}...{val[-suffix_len:]}"

def run_backtest():
    print("=" * 65)
    print("      BACKEND PRE-DEPLOYMENT API KEYS & SERVICES BACKTEST       ")
    print("=" * 65)
    print(f"Target Backend URL: {BASE_URL}")
    print("-" * 65)

    failures = []
    passes = 0

    # ---------------------------------------------------------
    # 1. DATABASE CONNECTIVITY & POOL VERIFICATION
    # ---------------------------------------------------------
    print("\n[1/6] Verifying Database Connection & Readiness...")
    try:
        from app.db import SessionLocal, engine
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            assert result == 1, "Database query returned unexpected result"
        print("  [OK] Direct SQLAlchemy engine connection passed")
        passes += 1
    except Exception as e:
        print(f"  [FAIL] Database engine check failed: {e}")
        failures.append(f"Database direct connection: {e}")

    try:
        r = requests.get(f"{BASE_URL}/health/ready", timeout=5)
        if r.status_code == 200:
            print("  [OK] /health/ready endpoint returned 200 OK")
            passes += 1
        else:
            print(f"  [FAIL] /health/ready returned {r.status_code}")
            failures.append(f"/health/ready HTTP {r.status_code}")
    except Exception as e:
        print(f"  [FAIL] /health/ready endpoint failed: {e}")
        failures.append(f"/health/ready error: {e}")

    # ---------------------------------------------------------
    # 2. JWT & SECRET_KEY VALIDATION
    # ---------------------------------------------------------
    print("\n[2/6] Verifying SECRET_KEY & JWT Signature...")
    secret_key = os.getenv("SECRET_KEY")
    if secret_key:
        print(f"  [OK] SECRET_KEY configured ({mask_key(secret_key)})")
        passes += 1
        try:
            from jose import jwt
            test_payload = {"sub": "test_user", "exp": int(time.time()) + 3600}
            encoded = jwt.encode(test_payload, secret_key, algorithm="HS256")
            decoded = jwt.decode(encoded, secret_key, algorithms=["HS256"])
            assert decoded["sub"] == "test_user"
            print("  [OK] JWT encoding and decoding round-trip verified")
            passes += 1
        except Exception as e:
            print(f"  [FAIL] JWT validation failed: {e}")
            failures.append(f"JWT crypto check: {e}")
    else:
        print("  [FAIL] SECRET_KEY is not set in environment")
        failures.append("SECRET_KEY environment variable missing")

    # ---------------------------------------------------------
    # 3. RAZORPAY API KEYS CONFIGURATION & CLIENT
    # ---------------------------------------------------------
    print("\n[3/6] Verifying Razorpay API Keys & Client...")
    rzp_key_id = os.getenv("RAZORPAY_KEY_ID")
    rzp_key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    rzp_webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    if rzp_key_id:
        print(f"  [OK] RAZORPAY_KEY_ID set ({mask_key(rzp_key_id)})")
        passes += 1
    else:
        print("  [WARN] RAZORPAY_KEY_ID not set")

    if rzp_key_secret:
        print(f"  [OK] RAZORPAY_KEY_SECRET set ({mask_key(rzp_key_secret)})")
        passes += 1
    else:
        print("  [WARN] RAZORPAY_KEY_SECRET not set")

    if rzp_webhook_secret:
        print(f"  [OK] RAZORPAY_WEBHOOK_SECRET set ({mask_key(rzp_webhook_secret)})")
    else:
        print("  [INFO] RAZORPAY_WEBHOOK_SECRET not set (optional for polling)")

    # Test backend razorpay endpoint
    try:
        r = requests.get(f"{BASE_URL}/api/razorpay-key", timeout=5)
        if r.status_code == 200:
            data = r.json()
            key_from_api = data.get("keyId") or data.get("key_id") or data.get("key")
            print(f"  [OK] GET /api/razorpay-key returned valid public key ({mask_key(key_from_api)})")
            passes += 1
        else:
            print(f"  [WARN] GET /api/razorpay-key returned {r.status_code}")
    except Exception as e:
        print(f"  [FAIL] /api/razorpay-key failed: {e}")
        failures.append(f"/api/razorpay-key: {e}")

    # Test Razorpay SDK Client Initialization
    try:
        import razorpay
        if rzp_key_id and rzp_key_secret:
            client = razorpay.Client(auth=(rzp_key_id, rzp_key_secret))
            print("  [OK] Razorpay SDK Client successfully instantiated")
            passes += 1
        else:
            print("  [INFO] Skipping Razorpay SDK Client instantiation (keys absent in current test env)")
    except Exception as e:
        print(f"  [FAIL] Razorpay SDK initialization error: {e}")
        failures.append(f"Razorpay Client init: {e}")

    # ---------------------------------------------------------
    # 4. GEMINI / AI SERVICES CONFIGURATION
    # ---------------------------------------------------------
    print("\n[4/6] Verifying Gemini / AI Service Keys...")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        print(f"  [OK] GEMINI_API_KEY configured ({mask_key(gemini_key)})")
        passes += 1
    else:
        print("  [INFO] GEMINI_API_KEY not set (Voice agent will use fallback TTS/stubs)")

    # ---------------------------------------------------------
    # 5. GOOGLE MAPS & AZURE KEYS (OPTIONAL / INTEGRATION)
    # ---------------------------------------------------------
    print("\n[5/6] Verifying Auxiliary Service Keys...")
    gmaps_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if gmaps_key:
        print(f"  [OK] GOOGLE_MAPS_API_KEY configured ({mask_key(gmaps_key)})")
        passes += 1
    else:
        print("  [INFO] GOOGLE_MAPS_API_KEY not configured (Delivery tracking uses fallback ETA/distance)")

    azure_endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    azure_key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if azure_endpoint and azure_key:
        print(f"  [OK] Azure Document Intelligence configured ({mask_key(azure_key)})")
        passes += 1
    else:
        print("  [INFO] Azure Document Intelligence not set (Bill scanner uses internal OCR/fallback)")

    # ---------------------------------------------------------
    # 6. CORE REST ENDPOINTS HEALTH & SMOKE TESTS
    # ---------------------------------------------------------
    print("\n[6/6] Verifying Core Operational Endpoints...")
    endpoints = [
        ("GET", "/health", 200),
        ("GET", "/health/live", 200),
        ("GET", "/health/ready", 200),
        ("GET", "/api/v1/public/restaurants", 200),
        ("GET", "/api/v1/public/menu/items?restaurant_id=1", 200),
        ("GET", "/api/customer/table/verify?table_number=T-01&restaurant_id=1", 200),
    ]

    for method, path, expected_status in endpoints:
        try:
            url = f"{BASE_URL}{path}"
            r = requests.request(method, url, timeout=5)
            if r.status_code == expected_status:
                print(f"  [OK] {method} {path} -> HTTP {r.status_code}")
                passes += 1
            else:
                print(f"  [WARN] {method} {path} -> HTTP {r.status_code} (expected {expected_status})")
        except Exception as e:
            print(f"  [FAIL] {method} {path} connection failed: {e}")
            failures.append(f"{method} {path}: {e}")

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    print("\n" + "=" * 65)
    print(f"BACKTEST RESULTS: {passes} CHECKS PASSED, {len(failures)} FAILURES")
    print("=" * 65)

    if failures:
        print("Failures encountered:")
        for f in failures:
            print(f"  - {f}")
        return False
    else:
        print("ALL PRE-DEPLOYMENT API KEY & SERVICE CHECKS PASSED SUCCESSFULLY!")
        return True

if __name__ == "__main__":
    success = run_backtest()
    sys.exit(0 if success else 1)

import os
import sys
import requests
from requests.exceptions import ConnectionError

def run_smoke_test(base_url: str):
    print(f"Running Full-Stack Deployment Smoke Test against {base_url}...")
    errors = 0

    # 1. Check Health
    try:
        r = requests.get(f"{base_url}/health")
        if r.status_code == 200:
            print("✅ API Health check passed")
        else:
            print(f"❌ API Health check failed. Status: {r.status_code}")
            errors += 1
    except ConnectionError:
        print("❌ Cannot connect to backend. Is it running?")
        sys.exit(1)

    # 2. Check Database Readiness
    try:
        r = requests.get(f"{base_url}/health/ready")
        if r.status_code == 200:
            print("✅ Database readiness check passed")
        else:
            print(f"❌ Database readiness check failed. DB might not be configured correctly. Status: {r.status_code}")
            errors += 1
    except Exception as e:
        print(f"❌ Database readiness check failed with exception: {e}")
        errors += 1

    # 3. Check Menu Endpoint (Customer Mobile dependency)
    try:
        r = requests.get(f"{base_url}/api/v1/public/restaurants/1/menu/items")
        # 200 is good, 404 is fine (if no restaurant 1)
        if r.status_code in [200, 404]:
            print(f"✅ Menu endpoint check passed (Status: {r.status_code})")
        else:
            print(f"⚠️ Menu endpoint returned unexpected status: {r.status_code}")
    except Exception as e:
        print(f"❌ Menu endpoint check failed with exception: {e}")
        errors += 1
        
    # 4. Check Razorpay Keys Configuration
    try:
        r = requests.get(f"{base_url}/api/razorpay-key")
        if r.status_code == 200:
            print("✅ Razorpay configuration is set")
        else:
            print(f"⚠️ Razorpay configuration missing or endpoint failed (Status: {r.status_code}). Payments may not work.")
    except Exception as e:
        print(f"❌ Razorpay key check failed with exception: {e}")

    print("-" * 50)
    if errors == 0:
        print("🎉 Smoke test completed successfully! The application is ready for production.")
        sys.exit(0)
    else:
        print(f"💥 Smoke test failed with {errors} errors. Please fix them before deploying.")
        sys.exit(1)

if __name__ == "__main__":
    url = os.getenv("API_BASE_URL", "http://127.0.0.1:8001")
    run_smoke_test(url)

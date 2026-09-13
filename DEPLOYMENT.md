# Deployment Guide

This guide outlines the production deployment requirements and configuration for the Full-Stack Food Ordering Application (Customer Mobile, Admin, POS, Rider, FastApi Backend).

## 1. Environment Variables

Your production server **must** contain a `.env` file at the root of the FastAPI project with the following required variables:

```env
# Core settings
ENVIRONMENT=production
DATABASE_URL=postgresql://user:password@localhost:5432/dbname

# CORS (Critical for Admin/POS web apps)
CORS_ALLOWED_ORIGINS=https://admin.dataudipi.com,https://pos.dataudipi.com

# Razorpay (Required for Payments)
RAZORPAY_KEY_ID=your_production_key_id
RAZORPAY_KEY_SECRET=your_production_key_secret

# Optional/Defaults
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=30
```

*Note: If `ENVIRONMENT=production` is set, `DATABASE_URL` is **mandatory**. The application will fail to start to prevent accidental fallback to SQLite in production.*

## 2. Running the Smoke Test

Before declaring the deployment successful, run the provided smoke test script to verify environment configuration and endpoint health.

```bash
cd Food_Ordering_App_Backend
python scripts/deployment_smoke_test.py
```
*(You can pass `API_BASE_URL` to override the target URL).*

## 3. Database Migrations

Always ensure your PostgreSQL schema is up to date:
```bash
alembic upgrade head
```

## 4. Frontend Configuration (Customer Mobile)

For the Expo React Native app (`mobileapp_frontend`):
When building for production (e.g., using EAS Build), ensure you set:
```env
EXPO_PUBLIC_API_BASE_URL=https://api.dataudipi.com
```

The frontend application uses `__DEV__` to enforce the presence of this URL in production and will refuse to fall back to `localhost` or `10.0.2.2` when compiled for production.

## 5. Security & Idempotency Notes

1. **Payments**: The `create-razorpay-order` and `verify-payment` endpoints strictly compute totals from the backend PostgreSQL database to prevent frontend tampering.
2. **Order Verification**: Item IDs, quantities, and prices are validated against the database during order creation.
3. **Location Snapshots**: When selecting a delivery address, the snapshot coordinates are securely constructed from the saved database address to prevent location spoofing.

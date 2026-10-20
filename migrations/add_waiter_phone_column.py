"""
Migration: Add nullable 'phone' column to users table.

SAFETY:
- Adds a nullable VARCHAR(20) column. Existing rows keep phone=NULL.
- Uniqueness enforced via a PARTIAL INDEX (WHERE phone IS NOT NULL),
  so existing NULL values never conflict with each other.
- Idempotent: safe to re-run if the column already exists.
- Does NOT modify, delete, or backfill any existing user records.

ROLLBACK:
  DROP INDEX ix_users_phone_unique;
  ALTER TABLE users DROP COLUMN phone;

PRODUCTION:
  DO NOT run against the live DB until explicitly approved.
  Pass --production-confirmed to override the safety guard.
"""
import sys
import os

db_url = os.getenv("DATABASE_URL", "")
is_production_confirmed = "--production-confirmed" in sys.argv

PROD_MARKERS = [
    "render.com", "supabase.io", "amazonaws.com", "neon.tech",
    "planetscale.com", "railway.app", "heroku.com", "dataudipi"
]
is_likely_prod = any(m in db_url.lower() for m in PROD_MARKERS)

if is_likely_prod and not is_production_confirmed:
    print("=" * 70)
    print("BLOCKED: Production database URL detected.")
    print("Migration NOT applied to production.")
    print("To apply, re-run with --production-confirmed.")
    print("=" * 70)
    sys.exit(1)

from app.db import engine
from sqlalchemy import inspect as sa_inspect, text

insp = sa_inspect(engine)

print("=" * 70)
print("MIGRATION: Add nullable phone column to users")
print(f"DB driver: {engine.url.drivername}")
print("=" * 70)

with engine.connect() as conn:
    existing_cols = [c["name"] for c in insp.get_columns("users")]
    print(f"Current columns: {existing_cols}")

    if "phone" in existing_cols:
        print("\nSKIPPED: phone column already exists. No changes made.")
        sys.exit(0)

    row_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    print(f"\nExisting rows: {row_count} (will all keep phone=NULL)")

    conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL"))
    print("DONE: ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL")

    conn.execute(text(
        "CREATE UNIQUE INDEX ix_users_phone_unique "
        "ON users (phone) WHERE phone IS NOT NULL"
    ))
    print("DONE: Created partial unique index on users.phone WHERE phone IS NOT NULL")

    conn.commit()

print("\nMigration complete.")
print("  Added: users.phone VARCHAR(20) NULL")
print("  Added: ix_users_phone_unique (partial, non-null only)")
print("  Existing rows: unchanged (phone=NULL)")
print("\nROLLBACK SQL:")
print("  DROP INDEX ix_users_phone_unique;")
print("  ALTER TABLE users DROP COLUMN phone;")
print("=" * 70)

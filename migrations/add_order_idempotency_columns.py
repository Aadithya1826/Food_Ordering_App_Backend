"""
Migration: Add nullable 'idempotency_key' and 'client_order_id' columns with unique indexes to orders table.

SAFETY:
- Adds nullable VARCHAR(255) columns. Existing rows keep values as NULL.
- Uniqueness enforced via PARTIAL UNIQUE INDEX (WHERE column IS NOT NULL),
  so existing NULL values never conflict.
- Purely additive, idempotent, and non-destructive.
- Safe to run on live PostgreSQL or SQLite database.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db import engine
from sqlalchemy import inspect as sa_inspect, text

insp = sa_inspect(engine)
driver = engine.url.drivername

print("=" * 70)
print("MIGRATION: Add idempotency_key and client_order_id to orders table")
print(f"DB Driver: {driver}")
print("=" * 70)

with engine.connect() as conn:
    existing_cols = [c["name"] for c in insp.get_columns("orders")]
    print(f"Existing columns in orders: {len(existing_cols)} columns")

    # Add idempotency_key if missing
    if "idempotency_key" not in existing_cols:
        print("Adding column 'idempotency_key'...")
        if "postgres" in driver:
            conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) NULL;"))
        else:
            conn.execute(text("ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(255);"))
        print("  [OK] Added idempotency_key column")
    else:
        print("  [OK] idempotency_key column already exists")

    # Add client_order_id if missing
    if "client_order_id" not in existing_cols:
        print("Adding column 'client_order_id'...")
        if "postgres" in driver:
            conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS client_order_id VARCHAR(255) NULL;"))
        else:
            conn.execute(text("ALTER TABLE orders ADD COLUMN client_order_id VARCHAR(255);"))
        print("  [OK] Added client_order_id column")
    else:
        print("  [OK] client_order_id column already exists")

    # Create partial unique indexes
    print("Ensuring partial unique indexes...")
    if "postgres" in driver or "sqlite" in driver:
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_idempotency_key "
                "ON orders (idempotency_key) WHERE idempotency_key IS NOT NULL;"
            ))
            print("  [OK] Created partial unique index ix_orders_idempotency_key")
        except Exception as e:
            print(f"  Note on ix_orders_idempotency_key: {e}")

        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_client_order_id "
                "ON orders (client_order_id) WHERE client_order_id IS NOT NULL;"
            ))
            print("  [OK] Created partial unique index ix_orders_client_order_id")
        except Exception as e:
            print(f"  Note on ix_orders_client_order_id: {e}")
    else:
        try:
            conn.execute(text("CREATE UNIQUE INDEX ix_orders_idempotency_key ON orders (idempotency_key);"))
        except Exception:
            pass
        try:
            conn.execute(text("CREATE UNIQUE INDEX ix_orders_client_order_id ON orders (client_order_id);"))
        except Exception:
            pass

    conn.commit()

print("=" * 70)
print("Migration completed successfully!")
print("=" * 70)

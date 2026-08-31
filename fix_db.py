import sys
from dotenv import load_dotenv
load_dotenv('/home/aadithya-s/Desktop/Projects/Food_Ordering_App/.env')
from app.db import engine
from sqlalchemy import text

alter_statements = [
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS latitude FLOAT;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS longitude FLOAT;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS gst_number VARCHAR;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS opening_time VARCHAR;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS closing_time VARCHAR;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS order_notifications INTEGER DEFAULT 1;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS low_stock_alerts INTEGER DEFAULT 1;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS daily_email_reports INTEGER DEFAULT 1;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS auto_print_bills INTEGER DEFAULT 1;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS print_kot INTEGER DEFAULT 0;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS tax_rate FLOAT DEFAULT 5.0;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS service_charge FLOAT DEFAULT 0.0;",
    "ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS packaging_charge FLOAT DEFAULT 10.0;",
    
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_address_snapshot JSONB;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_instructions VARCHAR;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_status VARCHAR;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee FLOAT;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS packaging_fee FLOAT;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS gst_amount FLOAT;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tip_amount FLOAT;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_type VARCHAR DEFAULT 'DINE_IN';"
]

try:
    with engine.begin() as conn:
        for stmt in alter_statements:
            print(f"Executing: {stmt}")
            conn.execute(text(stmt))
    print("Successfully updated database schema!")
except Exception as e:
    print(f"Error: {e}")

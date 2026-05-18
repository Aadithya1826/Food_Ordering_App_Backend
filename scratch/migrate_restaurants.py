import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import text
from app.db import engine

def run_migration():
    with engine.connect() as conn:
        print("Adding settings columns to restaurants table...")
        
        columns = [
            "gst_number VARCHAR",
            "opening_time VARCHAR",
            "closing_time VARCHAR",
            "order_notifications INTEGER DEFAULT 1",
            "low_stock_alerts INTEGER DEFAULT 1",
            "daily_email_reports INTEGER DEFAULT 1",
            "auto_print_bills INTEGER DEFAULT 1",
            "print_kot INTEGER DEFAULT 0",
            "tax_rate FLOAT DEFAULT 5.0",
            "service_charge FLOAT DEFAULT 0.0",
            "packaging_charge FLOAT DEFAULT 10.0"
        ]
        
        for col in columns:
            try:
                conn.execute(text(f"ALTER TABLE restaurants ADD COLUMN {col};"))
                print(f"Added {col} column.")
            except Exception as e:
                print(f"Column {col} might already exist: {e}")
                
        conn.commit()
        print("Migration complete.")

if __name__ == "__main__":
    run_migration()

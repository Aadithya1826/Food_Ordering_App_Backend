import os
import sys

# Add backend directory to sys.path so we can import from app
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import text
from app.db import engine, SessionLocal
from app.models.table import Table

def run_migration():
    with engine.connect() as conn:
        print("Adding capacity and is_active columns...")
        try:
            conn.execute(text("ALTER TABLE tables ADD COLUMN capacity INTEGER DEFAULT 4;"))
            print("Added capacity column.")
        except Exception as e:
            print(f"Column capacity might already exist: {e}")
            
        try:
            conn.execute(text("ALTER TABLE tables ADD COLUMN is_active BOOLEAN DEFAULT TRUE;"))
            print("Added is_active column.")
        except Exception as e:
            print(f"Column is_active might already exist: {e}")
            
        conn.commit()

    print("Inserting mock tables...")
    db = SessionLocal()
    try:
        # Check if tables already exist
        existing_tables_count = db.query(Table).count()
        mock_data = [
            {"table_number": "T-01", "capacity": 2, "is_active": True},
            {"table_number": "T-02", "capacity": 4, "is_active": True},
            {"table_number": "T-03", "capacity": 4, "is_active": True},
            {"table_number": "T-04", "capacity": 6, "is_active": True},
            {"table_number": "T-05", "capacity": 2, "is_active": True},
            {"table_number": "T-06", "capacity": 4, "is_active": True},
            {"table_number": "T-07", "capacity": 8, "is_active": True},
            {"table_number": "T-08", "capacity": 4, "is_active": True},
            {"table_number": "T-09", "capacity": 2, "is_active": True},
            {"table_number": "T-10", "capacity": 6, "is_active": True},
            {"table_number": "T-11", "capacity": 4, "is_active": True},
            {"table_number": "T-12", "capacity": 2, "is_active": True},
            {"table_number": "T-13", "capacity": 4, "is_active": True},
            {"table_number": "T-14", "capacity": 4, "is_active": True},
            {"table_number": "T-15", "capacity": 2, "is_active": False},
        ]
        
        for item in mock_data:
            t = db.query(Table).filter(Table.table_number == item["table_number"]).first()
            if not t:
                t = Table(
                    restaurant_id=1, 
                    table_number=item["table_number"], 
                    qr_code=f"https://example.com/qr/{item['table_number']}",
                    capacity=item["capacity"],
                    is_active=item["is_active"]
                )
                db.add(t)
            else:
                t.capacity = item["capacity"]
                t.is_active = item["is_active"]
        
        db.commit()
        print("Successfully ensured 15 mock tables exist.")
    except Exception as e:
        print(f"Error inserting tables: {e}")
        db.rollback()
    finally:
        db.close()
        
if __name__ == "__main__":
    run_migration()

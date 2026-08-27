import os
from sqlalchemy import create_engine, text

# Parse .env manually
env_file = "/home/aadithya-s/Desktop/Projects/Food_Ordering_App/.env"
DATABASE_URL = None
with open(env_file, 'r') as f:
    for line in f:
        if line.startswith("DATABASE_URL="):
            DATABASE_URL = line.strip().split("=", 1)[1]
            break

if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
    print("PostgreSQL DATABASE_URL not found or invalid.")
    exit(1)

print(f"Connecting to PostgreSQL: {DATABASE_URL.split('@')[-1]}")
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    try:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS delivery_addresses (
            id SERIAL PRIMARY KEY,
            restaurant_id INTEGER REFERENCES restaurants(id),
            name VARCHAR,
            phone VARCHAR,
            address_line VARCHAR,
            city VARCHAR,
            pincode VARCHAR
        )
        """))
        print("- delivery_addresses table created/exists.")
        
        try:
            conn.execute(text("ALTER TABLE orders ADD COLUMN order_type VARCHAR DEFAULT 'DINE_IN'"))
            print("- Added order_type column.")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("- order_type column already exists.")
            else:
                print(f"Error adding order_type: {e}")
                
        try:
            conn.execute(text("ALTER TABLE orders ADD COLUMN delivery_address_id INTEGER REFERENCES delivery_addresses(id)"))
            print("- Added delivery_address_id column.")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("- delivery_address_id column already exists.")
            else:
                print(f"Error adding delivery_address_id: {e}")
                
        conn.commit()
        print("PostgreSQL migration successful.")
    except Exception as e:
        print(f"Migration failed: {e}")

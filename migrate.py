import sqlite3
import os

DB_FILES = ['app.db', 'restaurant.db', 'food_ordering.db']

for db_file in DB_FILES:
    if os.path.exists(db_file):
        print(f"Migrating {db_file}...")
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # 1. Create delivery_addresses table
        try:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS delivery_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                restaurant_id INTEGER,
                name VARCHAR,
                phone VARCHAR,
                address_line VARCHAR,
                city VARCHAR,
                pincode VARCHAR,
                FOREIGN KEY(restaurant_id) REFERENCES restaurants(id)
            )
            """)
            print("- delivery_addresses table created/exists.")
        except Exception as e:
            print(f"Error creating delivery_addresses in {db_file}: {e}")
            
        # 2. Add order_type to orders
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN order_type VARCHAR DEFAULT 'DINE_IN'")
            print("- Added order_type column.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("- order_type column already exists.")
            else:
                print(f"Error adding order_type in {db_file}: {e}")
                
        # 3. Add delivery_address_id to orders
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN delivery_address_id INTEGER REFERENCES delivery_addresses(id)")
            print("- Added delivery_address_id column.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print("- delivery_address_id column already exists.")
            else:
                print(f"Error adding delivery_address_id in {db_file}: {e}")
                
        conn.commit()
        conn.close()
        print(f"Finished {db_file}.\n")

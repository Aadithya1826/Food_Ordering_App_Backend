from app.db import SessionLocal
from app.models.table import Table
from app.models.restaurant import Restaurant

def seed_tables():
    db = SessionLocal()
    print("=" * 60)
    print("DATABASE SEEDING: TABLES & QR CODES")
    print("=" * 60)
    
    try:
        # Resolve restaurant_id
        restaurant_id = None
        restaurant = db.query(Restaurant).first()
        if restaurant:
            restaurant_id = restaurant.id
            print(f"Found restaurant: '{restaurant.name}' (ID: {restaurant_id})")
        else:
            print("No restaurant found in the database. Seeding tables with restaurant_id = None.")
            
        print("\nSeeding tables T-01 to T-15...")
        
        for i in range(1, 16):
            table_number = f"T-{i:02d}"
            qr_url = f"http://localhost:3000/?table={table_number}"
            
            # Check if table already exists
            table = db.query(Table).filter(Table.table_number == table_number).first()
            if table:
                # Update existing table's QR code and active state
                table.qr_code = qr_url
                table.is_active = True
                if restaurant_id and not table.restaurant_id:
                    table.restaurant_id = restaurant_id
                print(f"   [Update] Table {table_number} QR code: {qr_url}")
            else:
                # Insert new table
                new_table = Table(
                    table_number=table_number,
                    qr_code=qr_url,
                    capacity=4,
                    is_active=True,
                    restaurant_id=restaurant_id
                )
                db.add(new_table)
                print(f"   [Create] Table {table_number} QR code: {qr_url}")
                
        db.commit()
        print("\nDatabase seeding completed successfully!")
        
    except Exception as e:
        db.rollback()
        print(f"\nSeeding failed: {str(e)}")
    finally:
        db.close()
        print("=" * 60)

if __name__ == "__main__":
    seed_tables()

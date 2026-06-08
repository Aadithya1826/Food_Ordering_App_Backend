import os
import sys
from sqlalchemy import text

# Add the backend directory to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine, Base, SessionLocal
from app.models.inventory import InventoryItem
from app.models.restaurant import Restaurant

def run_migration_and_seeding():
    print("Running ALTER TABLE statements...")
    db = SessionLocal()
    try:
        # Check if quantity column exists, if so rename to balance
        db.execute(text("ALTER TABLE inventory_items RENAME COLUMN quantity TO balance;"))
    except Exception as e:
        print(f"Column quantity might not exist or already renamed: {e}")
        db.rollback()

    try:
        db.execute(text("ALTER TABLE inventory_items ADD COLUMN open_stock FLOAT DEFAULT 0.0;"))
        db.execute(text("ALTER TABLE inventory_items ADD COLUMN purchase FLOAT DEFAULT 0.0;"))
        db.execute(text("ALTER TABLE inventory_items ADD COLUMN total FLOAT DEFAULT 0.0;"))
        db.execute(text("ALTER TABLE inventory_items ADD COLUMN issue FLOAT DEFAULT 0.0;"))
        db.commit()
    except Exception as e:
        print(f"Columns might already exist: {e}")
        db.rollback()
        
    # We need a restaurant_id to attach these to. Let's find the first restaurant or default to 1.
    db = SessionLocal()
    restaurant = db.query(Restaurant).first()
    if not restaurant:
        print("No restaurant found! Creating a dummy restaurant...")
        restaurant = Restaurant(name="Default Restaurant", address="Unknown", phone="1234567890", email="admin@restaurant.com")
        db.add(restaurant)
        db.commit()
        db.refresh(restaurant)
    
    restaurant_id = restaurant.id
    
    print(f"Seeding data for restaurant ID: {restaurant_id}...")
    
    # Data extracted from the images
    stock_data = [
        {"name": "DOSAI RICE", "open_stock": 6, "purchase": 0, "total": 6, "issue": 0, "balance": 6},
        {"name": "MEALS RICE", "open_stock": 26, "purchase": 0, "total": 26, "issue": 2, "balance": 24},
        {"name": "ULUNDHU DHALL", "open_stock": 150, "purchase": 0, "total": 150, "issue": 25, "balance": 125},
        {"name": "IDLY RAVA", "open_stock": 130, "purchase": 0, "total": 130, "issue": 5, "balance": 125},
        {"name": "OIL", "open_stock": 2, "purchase": 2, "total": 4, "issue": 3, "balance": 1},
        {"name": "GINGILI OIL", "open_stock": 2, "purchase": 2, "total": 4, "issue": 0, "balance": 4},
        {"name": "DALDA", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "GHEE", "open_stock": 3, "purchase": 15, "total": 18, "issue": 5, "balance": 13},
        {"name": "MAIDA", "open_stock": 50, "purchase": 0, "total": 50, "issue": 20, "balance": 30},
        {"name": "SUGAR", "open_stock": 24, "purchase": 50, "total": 74, "issue": 14, "balance": 60},
        {"name": "WHEAT MAVOO", "open_stock": 100, "purchase": 0, "total": 100, "issue": 20, "balance": 80},
        {"name": "GREEN PATTANI", "open_stock": 15, "purchase": 0, "total": 15, "issue": 0, "balance": 15},
        {"name": "BOMBAY RAVA", "open_stock": 20, "purchase": 0, "total": 20, "issue": 0, "balance": 20},
        {"name": "CHENNA KADALAI", "open_stock": 17, "purchase": 0, "total": 17, "issue": 3, "balance": 14},
        {"name": "MANJAL POWDER", "open_stock": 10, "purchase": 0, "total": 10, "issue": 5, "balance": 5},
        {"name": "DANIA POWDER", "open_stock": 18, "purchase": 0, "total": 18, "issue": 3, "balance": 15},
        {"name": "CHILLI POWDER", "open_stock": 10, "purchase": 10, "total": 20, "issue": 5, "balance": 15},
        {"name": "MILAGU", "open_stock": 2.5, "purchase": 5, "total": 7.5, "issue": 0.5, "balance": 7},
        {"name": "JEERAGAM", "open_stock": 2, "purchase": 5, "total": 7, "issue": 1, "balance": 6},
        {"name": "KADUGU", "open_stock": 5, "purchase": 4, "total": 9, "issue": 1, "balance": 8},
        {"name": "POONDU", "open_stock": 25, "purchase": 0, "total": 25, "issue": 3, "balance": 22},
        {"name": "COCONUT", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "BASMATHI RICE", "open_stock": 17, "purchase": 0, "total": 17, "issue": 5, "balance": 12},
        {"name": "NOODLES", "open_stock": 40, "purchase": 50, "total": 90, "issue": 10, "balance": 80},
        {"name": "MASALA POWDER", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "CORN FLOUR", "open_stock": 4, "purchase": 5, "total": 9, "issue": 1, "balance": 8},
        {"name": "SAUSE - CHILLI/TOMATO", "open_stock": 8, "purchase": 5, "total": 13, "issue": 1, "balance": 12},
        {"name": "PANNEER", "open_stock": 2, "purchase": 3, "total": 5, "issue": 2, "balance": 3},
        {"name": "MUSHROOM", "open_stock": 12, "purchase": 7, "total": 19, "issue": 6, "balance": 13},
        {"name": "CASHEWNUT", "open_stock": 2, "purchase": 3, "total": 5, "issue": 0, "balance": 5},
        {"name": "TANDURI GRAVEY", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        
        # Page 2
        {"name": "CARRY BAG", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "PARCEL PAPER", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "SAMBAR COVER", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "MILK", "open_stock": 11, "purchase": 36, "total": 47, "issue": 46, "balance": 1},
        {"name": "CURD", "open_stock": 8, "purchase": 36, "total": 44, "issue": 44, "balance": 0},
        {"name": "COFFEE POWDER", "open_stock": 3, "purchase": 0, "total": 3, "issue": 0.8, "balance": 2.2},
        {"name": "20 DAY PODI TEA", "open_stock": 1.5, "purchase": 0, "total": 1.5, "issue": 0.5, "balance": 1},
        {"name": "PARUPPUPODI", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "LG KATTI", "open_stock": 4, "purchase": 40, "total": 44, "issue": 4, "balance": 40},
        {"name": "APPALAM", "open_stock": 4, "purchase": 0, "total": 4, "issue": 4, "balance": 0},
        {"name": "MORE MILAGAI", "open_stock": 0, "purchase": 0, "total": 0, "issue": 0, "balance": 0},
        {"name": "Toor Dall", "open_stock": 70, "purchase": 50, "total": 120, "issue": 45, "balance": 75},
        {"name": "Moon Dall", "open_stock": 30, "purchase": 0, "total": 30, "issue": 0, "balance": 30},
    ]

    for item_data in stock_data:
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.restaurant_id == restaurant_id,
            InventoryItem.name.ilike(item_data["name"])
        ).first()
        
        if existing_item:
            existing_item.open_stock = item_data["open_stock"]
            existing_item.purchase = item_data["purchase"]
            existing_item.total = item_data["total"]
            existing_item.issue = item_data["issue"]
            existing_item.balance = item_data["balance"]
        else:
            item = InventoryItem(
                restaurant_id=restaurant_id,
                name=item_data["name"],
                open_stock=item_data["open_stock"],
                purchase=item_data["purchase"],
                total=item_data["total"],
                issue=item_data["issue"],
                balance=item_data["balance"],
                unit="units" # Defaulting to 'units' as requested
            )
            db.add(item)
    
    db.commit()
    db.close()
    print("Migration and seeding completed successfully!")

if __name__ == "__main__":
    run_migration_and_seeding()

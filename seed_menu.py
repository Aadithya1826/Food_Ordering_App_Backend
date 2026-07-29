from app.db import SessionLocal
from app.models.menu import MenuCategory, MenuItem
from app.models.restaurant import Restaurant

db = SessionLocal()

# Add a restaurant if it doesn't exist
restaurant = db.query(Restaurant).filter_by(id=1).first()
if not restaurant:
    restaurant = Restaurant(id=1, name="Data Udipi", address="Test Address", phone="1234567890")
    db.add(restaurant)
    db.commit()

# Categories
categories = [
    {"id": 1, "name": "all", "description": "All Menu"},
    {"id": 2, "name": "breakfast", "description": "Breakfast"},
    {"id": 3, "name": "lunch", "description": "Lunch"},
    {"id": 4, "name": "dinner", "description": "Dinner"}
]

for cat in categories:
    existing = db.query(MenuCategory).filter_by(name=cat["name"]).first()
    if not existing:
        db.add(MenuCategory(id=cat["id"], restaurant_id=1, name=cat["name"], description=cat["description"]))

db.commit()

# Menu Items
items = [
    { "id": 1, "name": "Onion Uttapam", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": True, "image_url": None, "category_id": 2 },
    { "id": 2, "name": "Masala Dosa", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": False, "image_url": None, "category_id": 2 },
    { "id": 3, "name": "Idli Vada", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": True, "image_url": None, "category_id": 2 },
    { "id": 4, "name": "Pongal", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": True, "image_url": None, "category_id": 3 },
    { "id": 5, "name": "Poori Sagu", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": True, "image_url": None, "category_id": 3 },
    { "id": 6, "name": "Rava Idli", "description": "semolina (rava), rice flour, maida, curd, and...", "price": 100, "is_available": True, "image_url": None, "category_id": 4 },
]

for item in items:
    existing = db.query(MenuItem).filter_by(id=item["id"]).first()
    if not existing:
        db.add(MenuItem(id=item["id"], restaurant_id=1, name=item["name"], description=item["description"], price=item["price"], is_available=item["is_available"], image_url=item["image_url"], category_id=item["category_id"]))

db.commit()
print("Seeding complete.")

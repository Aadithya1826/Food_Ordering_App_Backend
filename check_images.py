from app.db import SessionLocal
from app.models.menu import MenuItem

db = SessionLocal()
items = db.query(MenuItem).filter(MenuItem.id.in_([197, 198])).all()
for item in items:
    print(f"ID: {item.id}, Name: {item.name}, Image URL: {item.image_url}")

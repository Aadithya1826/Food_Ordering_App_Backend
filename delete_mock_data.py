from app.db import SessionLocal
from app.models.menu import MenuItem
from sqlalchemy import or_

db = SessionLocal()
items = db.query(MenuItem).filter(
    or_(
        MenuItem.id.in_([295, 296, 297]),
        MenuItem.name.ilike('%poorivadacurry%'),
        MenuItem.name.ilike('%poori vadacurry%'),
        MenuItem.name.ilike('%idiyappam vadacury%'),
        MenuItem.name.ilike('%idiyappam vadacurry%'),
        MenuItem.name.ilike('%onion masala dosai%'),
        MenuItem.name.ilike('%onion dosai masala%'),
        MenuItem.name.ilike('%onion dosai%'),
        MenuItem.name.ilike('%onion utappam%'),
        MenuItem.name.ilike('%onion uthappam%'),
        MenuItem.name.ilike('%tomato uthapam%'),
        MenuItem.name.ilike('%tomato uthappam%')
    )
).all()

for item in items:
    print(f"Deleting ID: {item.id}, Name: {item.name}")
    db.delete(item)

db.commit()
print("Successfully deleted the mock data items.")

from app.db import SessionLocal
from app.models.menu import MenuItem

db = SessionLocal()
high_items = db.query(MenuItem).filter(MenuItem.id > 190).all()
low_items = db.query(MenuItem).filter(MenuItem.id <= 190).all()

low_names = {item.name: item.id for item in low_items}
duplicates = []
unique_high = []

for item in high_items:
    if item.name in low_names:
        duplicates.append((item.id, item.name, low_names[item.name]))
    else:
        unique_high.append((item.id, item.name))

print(f"Total items > 190: {len(high_items)}")
print(f"Duplicates of lower IDs: {len(duplicates)}")
print(f"Unique high IDs: {len(unique_high)}")
if unique_high:
    print("Unique high items:", unique_high[:10])

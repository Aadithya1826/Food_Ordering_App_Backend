from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# Delete dependent order items first
db.execute(text("DELETE FROM order_items WHERE menu_item_id > 190"))

# Delete the menu items
result = db.execute(text("DELETE FROM menu_items WHERE id > 190"))

db.commit()
print(f"Successfully deleted {result.rowcount} duplicate/mock items and their associated test orders.")

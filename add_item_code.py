import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE menu_items ADD COLUMN item_code VARCHAR;"))
        conn.commit()
    print("Successfully added item_code to menu_items.")
except Exception as e:
    if "already exists" in str(e):
        print("Column item_code already exists.")
    else:
        print(f"Error: {e}")

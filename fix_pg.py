import os
from sqlalchemy import create_engine, text

env_file = "/home/aadithya-s/Desktop/Projects/Food_Ordering_App/.env"
DATABASE_URL = None
with open(env_file, 'r') as f:
    for line in f:
        if line.startswith("DATABASE_URL="):
            DATABASE_URL = line.strip().split("=", 1)[1]
            break

engine = create_engine(DATABASE_URL)
with engine.begin() as conn:
    try:
        conn.execute(text("ALTER TABLE orders ADD COLUMN order_type VARCHAR DEFAULT 'DINE_IN'"))
        print("- Added order_type column.")
    except Exception as e:
        print(e)

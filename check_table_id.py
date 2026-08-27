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
with engine.connect() as conn:
    result = conn.execute(text("SELECT data_type FROM information_schema.columns WHERE table_name='orders' AND column_name='table_id';"))
    for row in result:
        print(f"table_id data_type: {row[0]}")

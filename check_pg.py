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
    result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='orders';"))
    for row in result:
        print(row[0])

import os
from sqlalchemy import create_engine
from app.db import Base

# Import all models to ensure they are registered with Base
import app.models

# Explicitly import DeliveryAddress as it was newly added and maybe missing from __init__.py
from app.models.order import DeliveryAddress

env_file = "/home/aadithya-s/Desktop/Projects/Food_Ordering_App/.env"
DATABASE_URL = None
with open(env_file, 'r') as f:
    for line in f:
        if line.startswith("DATABASE_URL="):
            DATABASE_URL = line.strip().split("=", 1)[1]
            break

if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
    print("PostgreSQL DATABASE_URL not found or invalid.")
    exit(1)

print(f"Connecting to PostgreSQL: {DATABASE_URL.split('@')[-1]}")
engine = create_engine(DATABASE_URL)

try:
    Base.metadata.create_all(bind=engine)
    print("Successfully synchronized all missing database schemas to PostgreSQL!")
except Exception as e:
    print(f"Error synchronizing schema: {e}")

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
        conn.execute(text("UPDATE orders SET order_type = 'DELIVERY' WHERE delivery_address_id IS NOT NULL"))
        print("Updated DELIVERY orders.")
        
        conn.execute(text("UPDATE orders SET order_type = 'TAKEAWAY' WHERE table_id = 'takeaway' AND delivery_address_id IS NULL"))
        print("Updated TAKEAWAY orders based on table_id = 'takeaway'.")
        
        # Also, if table_id is NULL but it's not delivery, it might have been Takeaway based on backend logic
        conn.execute(text("UPDATE orders SET order_type = 'TAKEAWAY' WHERE table_id IS NULL AND delivery_address_id IS NULL AND order_type = 'DINE_IN'"))
        print("Updated TAKEAWAY orders based on table_id IS NULL.")
        
    except Exception as e:
        print(e)

import os
import psycopg2
from dotenv import load_dotenv

DATABASE_URL = os.getenv("DATABASE_URL")

def add_is_deleted():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()
        print("Connected. Adding is_deleted to menu_items...")
        cur.execute("ALTER TABLE menu_items ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE;")
        print("Column added successfully.")
        cur.close()
        conn.close()
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    add_is_deleted()

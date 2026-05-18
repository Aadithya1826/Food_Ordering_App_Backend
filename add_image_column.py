import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("DATABASE_URL not found in environment.")
    exit(1)

def alter_table():
    try:
        print("Connecting to database...")
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cursor = conn.cursor()
        
        print("Adding image_url column to menu_items table...")
        cursor.execute("ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS image_url VARCHAR;")
        
        print("Column added successfully.")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error altering table: {e}")

if __name__ == "__main__":
    alter_table()

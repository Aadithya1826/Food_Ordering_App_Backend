import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("DB_HOST", "banking-db.cnkegcm24ikf.ap-south-2.rds.amazonaws.com")
SRC_DB = os.getenv("SRC_DB", "food_ordering_db")
SRC_USER = os.getenv("SRC_USER", "food_admin")
SRC_PASS = os.getenv("SRC_PASS", "foodadmin@123")

TARGET_DB = os.getenv("TARGET_DB", "maitred_dev")
TARGET_USER = os.getenv("TARGET_USER", "maitred_dev_user")
TARGET_PASS = os.getenv("TARGET_PASS", "maitred_dev_pA$$W0rD")

print(f"Connecting source: {SRC_DB} and target: {TARGET_DB}...")
src_conn = psycopg2.connect(host=HOST, port=5432, user=SRC_USER, password=SRC_PASS, dbname=SRC_DB)
dst_conn = psycopg2.connect(host=HOST, port=5432, user=TARGET_USER, password=TARGET_PASS, dbname=TARGET_DB)
dst_conn.autocommit = False

src_cur = src_conn.cursor(cursor_factory=RealDictCursor)
dst_cur = dst_conn.cursor()

tables_to_copy = [
    "restaurants",
    "menu_categories",
    "menu_items",
    "tables",
    "catering_order_menu",
    "inventory_items",
    "menu_item_ingredients",
    "recipe_ingredients"
]

try:
    for table in tables_to_copy:
        print(f"Syncing {table}...")
        src_cur.execute(f'SELECT * FROM "{table}" ORDER BY id ASC;')
        rows = src_cur.fetchall()
        if not rows:
            print(f"  {table}: 0 rows to copy.")
            continue

        columns = list(rows[0].keys())
        col_names = ', '.join([f'"{c}"' for c in columns])
        placeholders = ', '.join(['%s'] * len(columns))
        
        insert_query = f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING;'
        data = [[r[c] for c in columns] for r in rows]
        dst_cur.executemany(insert_query, data)
        print(f"  Inserted {len(data)} rows into {table}.")
        
        try:
            dst_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM \"{table}\";")
        except Exception:
            pass

    dst_conn.commit()
    print(f"\nSuccessfully seeded catalog into {TARGET_DB}!")
except Exception as e:
    dst_conn.rollback()
    print("Error during seeding:", e)
    raise
finally:
    src_conn.close()
    dst_conn.close()

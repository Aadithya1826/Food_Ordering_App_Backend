import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))
from db import SessionLocal
from sqlalchemy import text

def reset():
    db = SessionLocal()
    try:
        db.execute(text("ALTER SEQUENCE orders_id_seq RESTART WITH 2001;"))
        db.commit()
        print("Successfully restarted sequence orders_id_seq to 2001.")
    except Exception as e:
        db.rollback()
        print(f"Error resetting sequence: {e}")
    finally:
        db.close()

if __name__ == '__main__':
    reset()

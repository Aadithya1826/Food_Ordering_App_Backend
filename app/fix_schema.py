"""
Database migration to add missing columns and update schema
Handles existing tables that need schema updates
"""
import sys
sys.path.insert(0, '/home/aadithya-s/Desktop/Projects/Food_Ordering_App')

from backend.app.db import engine
from sqlalchemy import inspect, text

print("=" * 70)
print("🔄 DATABASE SCHEMA MIGRATION")
print("=" * 70)

try:
    # Get inspector to check existing tables
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    with engine.connect() as connection:
        # Fix 1: Add description column to menu_categories if it doesn't exist
        if 'menu_categories' in existing_tables:
            columns = [col['name'] for col in inspector.get_columns('menu_categories')]
            if 'description' not in columns:
                print("\n📝 Adding 'description' column to menu_categories...")
                connection.execute(text(
                    "ALTER TABLE menu_categories ADD COLUMN description TEXT"
                ))
                connection.commit()
                print("   ✓ Added description column")
            else:
                print("\n✓ menu_categories already has description column")
        
        # Fix 2: Add updated_at column to orders if it doesn't exist
        if 'orders' in existing_tables:
            columns = [col['name'] for col in inspector.get_columns('orders')]
            if 'updated_at' not in columns:
                print("\n📝 Adding 'updated_at' column to orders...")
                connection.execute(text(
                    "ALTER TABLE orders ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ))
                connection.commit()
                print("   ✓ Added updated_at column")
            else:
                print("\n✓ orders already has updated_at column")
        
        connection.commit()
    
    print("\n" + "=" * 70)
    print("✅ Database schema migration completed successfully!")
    print("=" * 70)
    
except Exception as e:
    print(f"\n❌ Migration failed: {str(e)}")
    import traceback
    traceback.print_exc()
    print("=" * 70)
    sys.exit(1)

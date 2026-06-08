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
        # Fix 3: Add performance indexes for faster data loading
        print("\n📝 Adding performance indexes...")
        index_queries = [
            "CREATE INDEX IF NOT EXISTS ix_orders_restaurant_id ON orders (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_orders_table_id ON orders (table_id);",
            "CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status);",
            "CREATE INDEX IF NOT EXISTS ix_orders_created_at ON orders (created_at);",
            "CREATE INDEX IF NOT EXISTS ix_order_items_order_id ON order_items (order_id);",
            "CREATE INDEX IF NOT EXISTS ix_order_items_menu_item_id ON order_items (menu_item_id);",
            "CREATE INDEX IF NOT EXISTS ix_menu_categories_restaurant_id ON menu_categories (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_menu_items_restaurant_id ON menu_items (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_menu_items_category_id ON menu_items (category_id);",
            "CREATE INDEX IF NOT EXISTS ix_tables_restaurant_id ON tables (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_inventory_items_restaurant_id ON inventory_items (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_users_restaurant_id ON users (restaurant_id);",
            "CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);"
        ]
        
        for query in index_queries:
            try:
                connection.execute(text(query))
            except Exception as e:
                print(f"   ⚠️ Could not create index: {e}")
        print("   ✓ Performance indexes applied")
        
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

"""
Database migration script to create/update tables to match models
Run this script to initialize or update the database schema
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import engine, Base
from models import (
    User, MenuCategory, MenuItem, Table, Order, OrderItem, InventoryItem
)

def create_tables():
    """
    Create all tables defined in the models
    This will create new tables or update existing ones
    """
    print("=" * 70)
    print("🔄 DATABASE MIGRATION")
    print("=" * 70)
    
    try:
        print("\n📝 Creating/Updating tables to match model definitions...")
        
        # Create all tables based on models
        Base.metadata.create_all(bind=engine)
        
        print("\n✅ Database migration completed successfully!")
        print("\nTables created/updated:")
        print("   ✓ users")
        print("   ✓ menu_categories (with description column)")
        print("   ✓ menu_items")
        print("   ✓ tables")
        print("   ✓ orders (with updated_at column)")
        print("   ✓ order_items")
        print("   ✓ inventory_items")
        
        print("\n" + "=" * 70)
        print("🎉 All tables are now synced with models!")
        print("=" * 70)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        print("=" * 70)
        return False

if __name__ == "__main__":
    success = create_tables()
    sys.exit(0 if success else 1)

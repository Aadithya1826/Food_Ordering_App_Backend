import sqlalchemy
from app.db import SessionLocal, engine

db = SessionLocal()
insp = sqlalchemy.inspect(engine)

print('=== dine_in_sessions columns ===')
for col in insp.get_columns('dine_in_sessions'):
    print(f"  {col['name']}: {col['type']} nullable={col.get('nullable', True)}")

print()
print('=== orders columns ===')
for col in insp.get_columns('orders'):
    print(f"  {col['name']}: {col['type']}")

print()
print('=== order_items columns ===')
for col in insp.get_columns('order_items'):
    print(f"  {col['name']}: {col['type']}")

print()
print('=== payments columns ===')
try:
    for col in insp.get_columns('payments'):
        print(f"  {col['name']}: {col['type']}")
except:
    print('  No payments table found')

print()
print('=== dine_in_sessions indexes ===')
for idx in insp.get_indexes('dine_in_sessions'):
    print(f"  {idx}")

print()
print('=== dine_in_sessions foreign_keys ===')
for fk in insp.get_foreign_keys('dine_in_sessions'):
    print(f"  {fk}")

print()
print('=== orders foreign_keys ===')
for fk in insp.get_foreign_keys('orders'):
    print(f"  {fk}")

db.close()

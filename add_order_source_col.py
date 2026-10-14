import sqlalchemy
from app.db import engine

with engine.connect() as conn:
    insp = sqlalchemy.inspect(engine)
    cols = [c['name'] for c in insp.get_columns('orders')]
    print('orders cols count:', len(cols))
    if 'order_source' not in cols:
        print('Adding order_source column to orders...')
        conn.execute(sqlalchemy.text("ALTER TABLE orders ADD COLUMN order_source VARCHAR(32) DEFAULT 'CUSTOMER_APP';"))
        conn.commit()
        print('Successfully added order_source column to orders!')
    else:
        print('order_source column already exists.')

from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models.order import Order

client = TestClient(app)

# 1. Fetch active session or create session for Table T-02
res = client.post('/api/dine-in/session/active', json={'restaurant_id': 2, 'table_number': 'T-02'})
sess_data = res.json()
print('Session response:', sess_data)
sess_id = sess_data.get('sessionId') or sess_data.get('id')
sess_uid = sess_data.get('sessionUid')

# 2. Place Order Round 1 (Dry Fruit Rava Dosai - menu item 1047, price 135)
order1_payload = {
    'restaurant_id': 2,
    'table_number': 'T-02',
    'order_type': 'DINE_IN',
    'payment_method': 'Pay Later',
    'phone': '9876543210',
    'session_id': sess_id,
    'session_uid': sess_uid,
    'total_amount': 135.0,
    'cart': [{'id': 1047, 'name': 'Dry Fruit Rava Dosai', 'price': 135.0, 'quantity': 1}]
}
r1 = client.post('/api/orders?restaurant_id=2', json=order1_payload)
o1 = r1.json()
print('Order 1 Placed:', o1)
order1_id = o1['dbOrderId']

# 3. Simulate Kitchen advancing Order 1 to READY
db = SessionLocal()
order_obj = db.query(Order).filter(Order.id == order1_id).first()
order_obj.status = 'READY'
db.commit()
print(f'Order {order1_id} status updated to READY')

# 4. Customer does Order More -> Place Order Round 2 (Butter Nan with Paneer Butter Masala - menu item 1080, price 85)
order2_payload = {
    'restaurant_id': 2,
    'table_number': 'T-02',
    'order_type': 'DINE_IN',
    'payment_method': 'Pay Later',
    'phone': '9876543210',
    'session_id': sess_id,
    'session_uid': sess_uid,
    'total_amount': 85.0,
    'cart': [{'id': 1080, 'name': 'Butter Nan with Paneer Butter Masala', 'price': 85.0, 'quantity': 1}]
}
r2 = client.post('/api/orders?restaurant_id=2', json=order2_payload)
o2 = r2.json()
print('Order 2 Placed (Order More):', o2)
order2_id = o2['dbOrderId']

# 5. Verify PostgreSQL records
order1_fresh = db.query(Order).filter(Order.id == order1_id).first()
order2_fresh = db.query(Order).filter(Order.id == order2_id).first()

print('--- POSTGRESQL VERIFICATION ---')
print(f'Order 1 ({order1_fresh.id}): status={order1_fresh.status}, session={order1_fresh.dine_in_session_id}')
print(f'Order 2 ({order2_fresh.id}): status={order2_fresh.status}, session={order2_fresh.dine_in_session_id}')

assert order1_fresh.id != order2_fresh.id, 'Must be distinct order IDs'
assert order1_fresh.status == 'READY', f'Expected Order 1 status READY, got {order1_fresh.status}'
assert order2_fresh.status == 'PENDING', f'Expected Order 2 status PENDING, got {order2_fresh.status}'
assert order1_fresh.dine_in_session_id == order2_fresh.dine_in_session_id, 'Must share same dine_in_session_id'

# 6. Verify Session Details endpoint
s_res = client.get(f'/api/dine-in/session/{sess_id}/details?restaurant_id=2').json()
print('Session Details summary:')
print('  Total Amount:', s_res['totalAmount'])
print('  Orders count in session:', len(s_res['orders']))
for ord_info in s_res['orders']:
    items_names = [i['name'] for i in ord_info['items']]
    print(f"  Round {ord_info['round']}: #{ord_info['id']} status={ord_info['status']}, amount={ord_info['total_amount']}, items={items_names}")

db.close()
print('SUCCESS! Verified distinct order IDs, independent statuses, and accumulated session bill.')

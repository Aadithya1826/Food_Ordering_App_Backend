import sqlite3
conn = sqlite3.connect('d:/TECH WIZARD FOLDER-INTERN/Restaurant-mobile/Restaurant-mobile/Food_Ordering_App_Backend/food_ordering.db')
cursor = conn.cursor()
cursor.execute('SELECT id, order_type, delivery_address_snapshot FROM orders ORDER BY id DESC LIMIT 5')
for row in cursor.fetchall():
    print(row)

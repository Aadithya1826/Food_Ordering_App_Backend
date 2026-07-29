DELETE FROM menu_items
WHERE id IN (295, 296, 297)
   OR name ILIKE 'poorivadacurry'
   OR name ILIKE 'poori vadacurry'
   OR name ILIKE 'idiyappam vadacury'
   OR name ILIKE 'idiyappam vadacurry'
   OR name ILIKE 'onion dosai masala'
   OR name ILIKE 'onion masala dosai'
   OR name ILIKE 'onion dosai'
   OR name ILIKE 'onion utappam'
   OR name ILIKE 'onion uthappam'
   OR name ILIKE 'tomato uthapam'
   OR name ILIKE 'tomato uthappam';

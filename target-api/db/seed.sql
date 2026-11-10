-- Deterministic seed data (Phase 3.5C-2). psql variables (set by scripts/lab-seed.sh):
--   :users  :products  :orders  (row counts)    :seed  (setseed value in [-1, 1])
-- Same variables => byte-identical data: one seeded random stream, no parallel workers,
-- every random value drawn in a single-table scan in id order, and timestamps relative
-- to a fixed date (never now()).
SET max_parallel_workers_per_gather = 0;
SET synchronous_commit = off;
SELECT setseed(:seed);

INSERT INTO categories (id, name)
SELECT g, (ARRAY['Electronics','Home','Kitchen','Fashion','Sports','Books','Toys','Beauty',
                 'Garden','Office'])[1 + (g - 1) % 10] || ' ' || (1 + (g - 1) / 10)
FROM generate_series(1, 40) AS g
ORDER BY g;

INSERT INTO users (id, name, email, city, created_at)
SELECT g,
       'User ' || g,
       'user' || g || '@example.test',
       (ARRAY['Hyderabad','Mumbai','Delhi','Bengaluru','Chennai','Kolkata','Pune','Basar'])[1 + floor(random() * 8)::int],
       timestamptz '2025-01-01 00:00:00+00' + random() * interval '600 days'
FROM generate_series(1, :users) AS g
ORDER BY g;

-- description (~440 B) and specs (~1 KB) give products a realistic row width; the
-- large-payload fault (3.5C-3) returns them in bulk.
INSERT INTO products (id, category_id, name, description, specs, price, stock, created_at)
SELECT g,
       1 + floor(random() * 40)::int,
       (ARRAY['Smart','Classic','Ultra','Eco','Pro','Mini','Max','Prime'])[1 + floor(random() * 8)::int] || ' ' ||
       (ARRAY['Phone','Laptop','Chair','Lamp','Bottle','Shoe','Watch','Bag','Desk','Camera'])[1 + floor(random() * 10)::int] || ' ' || g,
       repeat('Detailed catalogue description for this product. ', 9),
       jsonb_build_object(
         'weight_g', floor(random() * 5000)::int,
         'color', (ARRAY['black','white','red','blue','green','grey'])[1 + floor(random() * 6)::int],
         'dimensions_cm', jsonb_build_array(floor(random() * 100)::int, floor(random() * 100)::int, floor(random() * 100)::int),
         'warranty_months', (ARRAY[6, 12, 24, 36])[1 + floor(random() * 4)::int],
         'notes', repeat('specification detail ', 40)),
       round((50 + random() * 49950)::numeric, 2),
       floor(random() * 500)::int,
       timestamptz '2025-01-01 00:00:00+00' + random() * interval '600 days'
FROM generate_series(1, :products) AS g
ORDER BY g;

-- Orders: users drawn with a skew (power 2), so some users have many orders and N+1
-- effects differ between pages; dates spread over the year before 2026-11-01.
CREATE TEMP TABLE gen_orders AS
SELECT g AS id,
       1 + floor(power(random(), 2) * :users)::bigint AS user_id,
       (ARRAY['placed','paid','shipped','delivered','delivered','delivered','cancelled'])[1 + floor(random() * 7)::int] AS status,
       timestamptz '2025-11-01 00:00:00+00' + random() * interval '365 days' AS created_at,
       1 + floor(random() * 5)::int AS n_items
FROM generate_series(1, :orders) AS g
ORDER BY g;

CREATE TEMP TABLE gen_items AS
SELECT o.id AS order_id, i AS line,
       1 + floor(random() * :products)::bigint AS product_id,
       1 + floor(random() * 3)::int AS quantity
FROM gen_orders AS o, generate_series(1, o.n_items) AS i
ORDER BY o.id, i;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT gi.order_id, gi.product_id, gi.quantity, p.price
FROM gen_items AS gi JOIN products AS p ON p.id = gi.product_id
ORDER BY gi.order_id, gi.line;

INSERT INTO orders (id, user_id, status, total, created_at)
SELECT o.id, o.user_id, o.status, coalesce(t.total, 0), o.created_at
FROM gen_orders AS o
LEFT JOIN (SELECT order_id, sum(quantity * unit_price) AS total FROM order_items GROUP BY order_id) AS t
       ON t.order_id = o.id
ORDER BY o.id;

DROP TABLE gen_items;
DROP TABLE gen_orders;

-- Identity columns continue after the seeded ids (new orders from POST /api/orders).
SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT max(id) FROM users));
SELECT setval(pg_get_serial_sequence('products', 'id'), (SELECT max(id) FROM products));
SELECT setval(pg_get_serial_sequence('orders', 'id'), (SELECT max(id) FROM orders));
SELECT setval(pg_get_serial_sequence('order_items', 'id'), (SELECT max(id) FROM order_items));

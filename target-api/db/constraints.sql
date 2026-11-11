-- Constraints and the healthy secondary indexes (Phase 3.5C-2), added after loading.
-- Index names are fixed: database faults (Phase 3.5C-3) remove specific indexes by name,
-- and their remediation recreates them with exactly these definitions.

ALTER TABLE users       ADD CONSTRAINT users_email_key UNIQUE (email);
ALTER TABLE products    ADD CONSTRAINT products_category_fk FOREIGN KEY (category_id) REFERENCES categories (id);
ALTER TABLE orders      ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users (id);
ALTER TABLE order_items ADD CONSTRAINT order_items_order_fk FOREIGN KEY (order_id) REFERENCES orders (id);
ALTER TABLE order_items ADD CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products (id);

CREATE INDEX orders_user_id_idx          ON orders (user_id);
CREATE INDEX orders_created_at_idx       ON orders (created_at);
CREATE INDEX order_items_order_id_idx    ON order_items (order_id);
CREATE INDEX order_items_product_id_idx  ON order_items (product_id);
CREATE INDEX products_category_price_idx ON products (category_id, price);
-- C collation: supports both prefix LIKE and ORDER BY lower(name) COLLATE "C" (search).
CREATE INDEX products_name_lower_idx     ON products ((lower(name) COLLATE "C"));

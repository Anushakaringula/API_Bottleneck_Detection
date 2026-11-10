// Domain API of the reference application (Phase 3.5C-2): an e-commerce back end on
// PostgreSQL + Redis. Only the healthy implementations are here; Phase 3.5C-3 adds the
// faulty and remediated variants of each operation (each fault in at least two operations).
//
// Operation       Route                       Work
// users.list      GET  /api/users              page of users with their order counts
// users.get       GET  /api/users/:id          one user and their 10 latest orders
// products.list   GET  /api/products           page of products (optionally one category)
// products.get    GET  /api/products/:id       product details, cache-aside in Redis (60 s)
// orders.list     GET  /api/orders?user_id=    a user's orders with item counts
// orders.create   POST /api/orders             place an order (transaction)
// search          GET  /api/search?q=          product name prefix search
// reports         GET  /api/reports?days=      sales by category, cached (60 s)
// dashboard       GET  /api/dashboard          summary figures (parallel queries)
const express = require("express");
const { HttpError, operation, intParam, idParam, cached } = require("./common");

const router = express.Router();

router.get("/api/users", operation("users.list", {
  async healthy(req, res, { db }) {
    const limit = intParam(req.query.limit, 20, 1, 100);
    const offset = intParam(req.query.offset, 0, 0, 10_000_000);
    const { rows } = await db.query(
      `SELECT u.id, u.name, u.city,
              (SELECT count(*)::int FROM orders o WHERE o.user_id = u.id) AS order_count
         FROM (SELECT id, name, city FROM users ORDER BY id LIMIT $1 OFFSET $2) u
        ORDER BY u.id`,
      [limit, offset],
    );
    res.json({ users: rows, limit, offset });
  },
}));

router.get("/api/users/:id", operation("users.get", {
  async healthy(req, res, { db }) {
    const id = idParam(req.params.id);
    const user = await db.query("SELECT id, name, email, city, created_at FROM users WHERE id = $1", [id]);
    if (!user.rows.length) throw new HttpError(404, "user not found");
    const orders = await db.query(
      "SELECT id, status, total, created_at FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT 10",
      [id],
    );
    res.json({ user: user.rows[0], recent_orders: orders.rows });
  },
}));

router.get("/api/products", operation("products.list", {
  async healthy(req, res, { db }) {
    const limit = intParam(req.query.limit, 20, 1, 100);
    const offset = intParam(req.query.offset, 0, 0, 10_000_000);
    const category = intParam(req.query.category, null, 1, 1000);
    const { rows } = await db.query(
      `SELECT id, name, price, stock, category_id FROM products
        WHERE ($1::int IS NULL OR category_id = $1)
        ORDER BY id LIMIT $2 OFFSET $3`,
      [category, limit, offset],
    );
    res.json({ products: rows, limit, offset });
  },
}));

async function loadProduct(db, id) {
  const { rows } = await db.query(
    `SELECT p.id, p.name, p.description, p.specs, p.price, p.stock, p.created_at,
            c.id AS category_id, c.name AS category,
            (SELECT count(*)::int FROM order_items oi WHERE oi.product_id = p.id) AS times_ordered
       FROM products p JOIN categories c ON c.id = p.category_id
      WHERE p.id = $1`,
    [id],
  );
  return rows[0] || null;
}

router.get("/api/products/:id", operation("products.get", {
  async healthy(req, res, { db }) {
    const id = idParam(req.params.id);
    const product = await cached(`product:${id}`, 60, () => loadProduct(db, id));
    if (!product) throw new HttpError(404, "product not found");
    res.json({ product });
  },
}));

router.get("/api/orders", operation("orders.list", {
  async healthy(req, res, { db }) {
    const userId = idParam(req.query.user_id);
    if (userId === undefined) throw new HttpError(400, "user_id is required");
    const limit = intParam(req.query.limit, 20, 1, 100);
    const { rows } = await db.query(
      `SELECT o.id, o.status, o.total, o.created_at, count(oi.id)::int AS items
         FROM orders o JOIN order_items oi ON oi.order_id = o.id
        WHERE o.user_id = $1
        GROUP BY o.id
        ORDER BY o.created_at DESC
        LIMIT $2`,
      [userId, limit],
    );
    res.json({ orders: rows, limit });
  },
}));

router.post("/api/orders", operation("orders.create", {
  async healthy(req, res, { db }) {
    const body = req.body || {};
    const userId = idParam(body.user_id);
    const items = Array.isArray(body.items) ? body.items : [];
    if (userId === undefined || !items.length || items.length > 20) throw new HttpError(400, "user_id and 1-20 items are required");
    const productIds = items.map((item) => idParam(item.product_id));
    const quantities = items.map((item) => intParam(item.quantity, 1, 1, 100));
    const client = await db.connect();
    try {
      await client.query("BEGIN");
      const prices = await client.query("SELECT id, price FROM products WHERE id = ANY($1::bigint[])", [productIds]);
      const priceById = new Map(prices.rows.map((row) => [String(row.id), Number(row.price)]));
      if (productIds.some((id) => !priceById.has(String(id)))) throw new HttpError(400, "unknown product");
      const unitPrices = productIds.map((id) => priceById.get(String(id)));
      const total = unitPrices.reduce((sum, price, i) => sum + price * quantities[i], 0);
      const order = await client.query(
        "INSERT INTO orders (user_id, status, total, created_at) VALUES ($1, 'placed', $2, now()) RETURNING id, status, total, created_at",
        [userId, total.toFixed(2)],
      );
      await client.query(
        `INSERT INTO order_items (order_id, product_id, quantity, unit_price)
         SELECT $1, * FROM unnest($2::bigint[], $3::int[], $4::numeric[])`,
        [order.rows[0].id, productIds, quantities, unitPrices],
      );
      await client.query("COMMIT");
      res.status(201).json({ order: { ...order.rows[0], items: items.length } });
    } catch (error) {
      await client.query("ROLLBACK").catch(() => {});
      if (error.code === "23503") throw new HttpError(400, "unknown user");
      throw error;
    } finally {
      client.release();
    }
  },
}));

router.get("/api/search", operation("search", {
  async healthy(req, res, { db }) {
    const q = String(req.query.q || "").trim().toLowerCase();
    if (q.length < 2 || q.length > 50) throw new HttpError(400, "q must be 2-50 characters");
    const limit = intParam(req.query.limit, 20, 1, 100);
    const pattern = q.replace(/[\\%_]/g, (c) => `\\${c}`) + "%";
    const { rows } = await db.query(
      // Matches the index expression (lower(name) COLLATE "C"), so the index serves both the
      // prefix match and the sort order, and LIMIT stops after the first rows.
      `SELECT id, name, price FROM products
        WHERE lower(name) COLLATE "C" LIKE $1 ORDER BY lower(name) COLLATE "C" LIMIT $2`,
      [pattern, limit],
    );
    res.json({ results: rows, limit });
  },
}));

// Reports use a fixed reference time, the end of the seeded order period (db/seed.sql),
// never now(): results stay identical from day to day, and orders placed during a run
// (created_at = now(), after the reference) never shift the reporting windows.
const REFERENCE_TIME = "2026-11-01T00:00:00Z";
const SALES_BY_CATEGORY = `
  SELECT c.id AS category_id, c.name AS category,
         count(DISTINCT o.id)::int AS orders, sum(oi.quantity)::int AS units,
         sum(oi.quantity * oi.unit_price)::numeric(14, 2) AS revenue
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN products p ON p.id = oi.product_id
    JOIN categories c ON c.id = p.category_id
   WHERE o.created_at > $2::timestamptz - make_interval(days => $1) AND o.created_at <= $2::timestamptz
   GROUP BY c.id, c.name
   ORDER BY revenue DESC`;

router.get("/api/reports", operation("reports", {
  async healthy(req, res, { db }) {
    const days = intParam(req.query.days, 30, 1, 365);
    const rows = await cached(`report:sales-by-category:${days}`, 60, async () => (await db.query(SALES_BY_CATEGORY, [days, REFERENCE_TIME])).rows);
    res.json({ days, categories: rows });
  },
}));

router.get("/api/dashboard", operation("dashboard", {
  async healthy(req, res, { db }) {
    const [users, recent, latest, top] = await Promise.all([
      db.query("SELECT count(*)::int AS n FROM users"),
      db.query(`SELECT count(*)::int AS n, coalesce(sum(total), 0)::numeric(14, 2) AS revenue
                  FROM orders WHERE created_at > $1::timestamptz - interval '1 day' AND created_at <= $1::timestamptz`, [REFERENCE_TIME]),
      db.query("SELECT id, user_id, status, total, created_at FROM orders ORDER BY created_at DESC LIMIT 10"),
      cached("dashboard:top-products:7", 60, async () => (await db.query(
        `SELECT p.id, p.name, sum(oi.quantity)::int AS units
           FROM orders o JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id
          WHERE o.created_at > $1::timestamptz - interval '7 days' AND o.created_at <= $1::timestamptz
          GROUP BY p.id, p.name ORDER BY units DESC, p.id LIMIT 5`, [REFERENCE_TIME])).rows),
    ]);
    res.json({
      users: users.rows[0].n,
      orders_last_day: recent.rows[0].n,
      revenue_last_day: recent.rows[0].revenue,
      latest_orders: latest.rows,
      top_products_last_week: top,
    });
  },
}));

module.exports = router;

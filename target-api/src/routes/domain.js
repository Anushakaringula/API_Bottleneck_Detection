// Domain API of the reference application: an e-commerce back end on PostgreSQL + Redis
// (Phase 3.5C-2), with injected faults and their fixes (Phase 3.5C-3).
//
// Operation       Route                       Healthy work                                   Variants
// users.list      GET  /api/users              users page with order counts (1 query)        nplus1, nplus1-fixed
// users.get       GET  /api/users/:id          user + latest orders with item counts         nplus1, nplus1-fixed
// products.list   GET  /api/products           products page, summary columns                payload, payload-fixed
// products.get    GET  /api/products/:id       product details, Redis cache-aside (60 s)     cache, cache-fixed, cpu, cpu-fixed
// orders.list     GET  /api/orders?user_id=    a user's orders with item counts              noindex, noindex-fixed
// orders.create   POST /api/orders             place an order (transaction)                  -
// search          GET  /api/search?q=          product name prefix search                    noindex, noindex-fixed, payload, payload-fixed
// reports         GET  /api/reports?days=      sales by category, Redis cache (60 s)         cache, cache-fixed
// dashboard       GET  /api/dashboard          summary figures (parallel queries)            cpu, cpu-fixed
//
// Fault classes (ground truth, never in telemetry): nplus1 = N_PLUS_ONE, noindex = DB_BOTTLENECK,
// payload = LARGE_PAYLOAD, cache = CACHE_OPPORTUNITY, cpu = CPU_BOTTLENECK. A "-fixed" variant
// is the remediation of that fault and returns the same data as the faulty variant (same
// page size etc.), so before/after comparisons measure only the fix. Severities:
//   nplus1   rows per response (users.list default 20, users.get default 10)
//   payload  rows returned with all columns (products.list default 2000, search default 1000)
//   cpu      thousands of pbkdf2 iterations per response (default 100: about +20 ms on the
//            project PC, where 20 gave only +4 ms; calibrated by scripts/fault-checks.mjs)
//   noindex, cache: no severity (noindex scales with the seed size)
const express = require("express");
const { HttpError, operation, intParam, idParam, cached } = require("./common");
const { dropIndex, ensureIndex, severity, expensiveSignature, cheapSignature, brokenCacheKey } = require("../lab/faults");

const router = express.Router();

// ---------------------------------------------------------------- users.list
async function usersPage(db, limit, offset) {
  const { rows } = await db.query(
    `SELECT u.id, u.name, u.city,
            (SELECT count(*)::int FROM orders o WHERE o.user_id = u.id) AS order_count
       FROM (SELECT id, name, city FROM users ORDER BY id LIMIT $1 OFFSET $2) u
      ORDER BY u.id`,
    [limit, offset],
  );
  return rows;
}

router.get("/api/users", operation("users.list", {
  async healthy(req, res, { db }) {
    const limit = intParam(req.query.limit, 20, 1, 100);
    const offset = intParam(req.query.offset, 0, 0, 10_000_000);
    res.json({ users: await usersPage(db, limit, offset), limit, offset });
  },
  // N+1: the page, then one count query per user, one after another.
  async nplus1(req, res, { db, variant }) {
    const limit = severity(variant, 20, 1, 1000);
    const offset = intParam(req.query.offset, 0, 0, 10_000_000);
    const { rows } = await db.query("SELECT id, name, city FROM users ORDER BY id LIMIT $1 OFFSET $2", [limit, offset]);
    for (const user of rows) {
      const count = await db.query("SELECT count(*)::int AS n FROM orders WHERE user_id = $1", [user.id]);
      user.order_count = count.rows[0].n;
    }
    res.json({ users: rows, limit, offset });
  },
  async "nplus1-fixed"(req, res, { db, variant }) {
    const limit = severity(variant, 20, 1, 1000);
    const offset = intParam(req.query.offset, 0, 0, 10_000_000);
    res.json({ users: await usersPage(db, limit, offset), limit, offset });
  },
}));

// ---------------------------------------------------------------- users.get
async function loadUser(db, id) {
  const user = await db.query("SELECT id, name, email, city, created_at FROM users WHERE id = $1", [id]);
  if (!user.rows.length) throw new HttpError(404, "user not found");
  return user.rows[0];
}

async function recentOrdersWithItems(db, userId, count) {
  const { rows } = await db.query(
    `SELECT o.id, o.status, o.total, o.created_at, count(oi.id)::int AS items
       FROM (SELECT id, status, total, created_at FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2) o
       JOIN order_items oi ON oi.order_id = o.id
      GROUP BY o.id, o.status, o.total, o.created_at
      ORDER BY o.created_at DESC`,
    [userId, count],
  );
  return rows;
}

router.get("/api/users/:id", operation("users.get", {
  async healthy(req, res, { db }) {
    const id = idParam(req.params.id);
    const user = await loadUser(db, id);
    res.json({ user, recent_orders: await recentOrdersWithItems(db, id, 10) });
  },
  // N+1: the orders, then one item-count query per order.
  async nplus1(req, res, { db, variant }) {
    const count = severity(variant, 10, 1, 1000);
    const id = idParam(req.params.id);
    const user = await loadUser(db, id);
    const orders = await db.query(
      "SELECT id, status, total, created_at FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
      [id, count],
    );
    for (const order of orders.rows) {
      const items = await db.query("SELECT count(*)::int AS n FROM order_items WHERE order_id = $1", [order.id]);
      order.items = items.rows[0].n;
    }
    res.json({ user, recent_orders: orders.rows });
  },
  async "nplus1-fixed"(req, res, { db, variant }) {
    const count = severity(variant, 10, 1, 1000);
    const id = idParam(req.params.id);
    const user = await loadUser(db, id);
    res.json({ user, recent_orders: await recentOrdersWithItems(db, id, count) });
  },
}));

// ---------------------------------------------------------------- products.list
async function productsPage(db, req) {
  const limit = intParam(req.query.limit, 20, 1, 100);
  const offset = intParam(req.query.offset, 0, 0, 10_000_000);
  const category = intParam(req.query.category, null, 1, 1000);
  const { rows } = await db.query(
    `SELECT id, name, price, stock, category_id FROM products
      WHERE ($1::int IS NULL OR category_id = $1)
      ORDER BY id LIMIT $2 OFFSET $3`,
    [category, limit, offset],
  );
  return { products: rows, limit, offset };
}

router.get("/api/products", operation("products.list", {
  async healthy(req, res, { db }) {
    res.json(await productsPage(db, req));
  },
  // LARGE_PAYLOAD: pagination ignored, every column (description, specs) of many rows.
  async payload(req, res, { db, variant }) {
    const rows = severity(variant, 2000, 1, 50000);
    const category = intParam(req.query.category, null, 1, 1000);
    const result = await db.query(
      `SELECT p.*, c.name AS category FROM products p JOIN categories c ON c.id = p.category_id
        WHERE ($1::int IS NULL OR p.category_id = $1)
        ORDER BY p.id LIMIT $2`,
      [category, rows],
    );
    res.json({ products: result.rows });
  },
  // Fix: pagination + field selection (= the healthy implementation).
  async "payload-fixed"(req, res, { db }) {
    res.json(await productsPage(db, req));
  },
}));

// ---------------------------------------------------------------- products.get
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

function sendProduct(res, product, signature) {
  if (!product) throw new HttpError(404, "product not found");
  if (signature) res.set("X-Content-Signature", signature);
  res.json({ product });
}

router.get("/api/products/:id", operation("products.get", {
  async healthy(req, res, { db }) {
    const id = idParam(req.params.id);
    sendProduct(res, await cached(`product:${id}`, 60, () => loadProduct(db, id)));
  },
  // CACHE_OPPORTUNITY: the cache is "there", but its key changes on every request.
  async cache(req, res, { db }) {
    const id = idParam(req.params.id);
    sendProduct(res, await cached(brokenCacheKey(`product:${id}`), 60, () => loadProduct(db, id)));
  },
  async "cache-fixed"(req, res, { db }) {
    const id = idParam(req.params.id);
    sendProduct(res, await cached(`product:${id}`, 60, () => loadProduct(db, id)));
  },
  // CPU_BOTTLENECK: an expensive key-derivation "signature" on every response.
  async cpu(req, res, { db, variant }) {
    const work = severity(variant, 100, 0.1, 1000);
    const id = idParam(req.params.id);
    const product = await cached(`product:${id}`, 60, () => loadProduct(db, id));
    sendProduct(res, product, product && expensiveSignature(product, work));
  },
  async "cpu-fixed"(req, res, { db }) {
    const id = idParam(req.params.id);
    const product = await cached(`product:${id}`, 60, () => loadProduct(db, id));
    sendProduct(res, product, product && cheapSignature(product));
  },
}));

// ---------------------------------------------------------------- orders.list
async function userOrders(req, db) {
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
  return { orders: rows, limit };
}

router.get("/api/orders", operation("orders.list", {
  async healthy(req, res, { db }) {
    res.json(await userOrders(req, db));
  },
  // DB_BOTTLENECK: same query, but the index on orders.user_id is missing (full scan of orders).
  noindex: {
    prepare: dropIndex("orders_user_id_idx"),
    async handle(req, res, { db }) {
      res.json(await userOrders(req, db));
    },
  },
  "noindex-fixed": {
    prepare: ensureIndex("orders_user_id_idx"),
    async handle(req, res, { db }) {
      res.json(await userOrders(req, db));
    },
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

// ---------------------------------------------------------------- search
function searchPattern(req) {
  const q = String(req.query.q || "").trim().toLowerCase();
  if (q.length < 2 || q.length > 50) throw new HttpError(400, "q must be 2-50 characters");
  return q.replace(/[\\%_]/g, (c) => `\\${c}`) + "%";
}

// Matches the index expression (lower(name) COLLATE "C"), so the index serves both the
// prefix match and the sort order, and LIMIT stops after the first rows.
async function searchSummary(req, db) {
  const pattern = searchPattern(req);
  const limit = intParam(req.query.limit, 20, 1, 100);
  const { rows } = await db.query(
    `SELECT id, name, price FROM products
      WHERE lower(name) COLLATE "C" LIKE $1 ORDER BY lower(name) COLLATE "C" LIMIT $2`,
    [pattern, limit],
  );
  return { results: rows, limit };
}

router.get("/api/search", operation("search", {
  async healthy(req, res, { db }) {
    res.json(await searchSummary(req, db));
  },
  // DB_BOTTLENECK: the name index is missing, so every search scans and sorts all products.
  noindex: {
    prepare: dropIndex("products_name_lower_idx"),
    async handle(req, res, { db }) {
      res.json(await searchSummary(req, db));
    },
  },
  "noindex-fixed": {
    prepare: ensureIndex("products_name_lower_idx"),
    async handle(req, res, { db }) {
      res.json(await searchSummary(req, db));
    },
  },
  // LARGE_PAYLOAD: full product rows for up to <severity> matches instead of a summary page.
  async payload(req, res, { db, variant }) {
    const rows = severity(variant, 1000, 1, 50000);
    const { rows: results } = await db.query(
      `SELECT p.*, c.name AS category FROM products p JOIN categories c ON c.id = p.category_id
        WHERE lower(p.name) COLLATE "C" LIKE $1 ORDER BY lower(p.name) COLLATE "C" LIMIT $2`,
      [searchPattern(req), rows],
    );
    res.json({ results });
  },
  async "payload-fixed"(req, res, { db }) {
    res.json(await searchSummary(req, db));
  },
}));

// ---------------------------------------------------------------- reports
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

async function salesReport(req, db, keyFor) {
  const days = intParam(req.query.days, 30, 1, 365);
  const rows = await cached(keyFor(`report:sales-by-category:${days}`), 60, async () => (await db.query(SALES_BY_CATEGORY, [days, REFERENCE_TIME])).rows);
  return { days, categories: rows };
}

const sameKey = (key) => key;

router.get("/api/reports", operation("reports", {
  async healthy(req, res, { db }) {
    res.json(await salesReport(req, db, sameKey));
  },
  // CACHE_OPPORTUNITY: every request recomputes the heavy aggregation (cache key never repeats).
  async cache(req, res, { db }) {
    res.json(await salesReport(req, db, brokenCacheKey));
  },
  async "cache-fixed"(req, res, { db }) {
    res.json(await salesReport(req, db, sameKey));
  },
}));

// ---------------------------------------------------------------- dashboard
async function dashboardFigures(db) {
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
  return {
    users: users.rows[0].n,
    orders_last_day: recent.rows[0].n,
    revenue_last_day: recent.rows[0].revenue,
    latest_orders: latest.rows,
    top_products_last_week: top,
  };
}

router.get("/api/dashboard", operation("dashboard", {
  async healthy(req, res, { db }) {
    res.json(await dashboardFigures(db));
  },
  // CPU_BOTTLENECK: the figures are "signed" with an expensive key derivation per request.
  async cpu(req, res, { db, variant }) {
    const work = severity(variant, 100, 0.1, 1000);
    const figures = await dashboardFigures(db);
    res.set("X-Content-Signature", expensiveSignature(figures, work));
    res.json(figures);
  },
  async "cpu-fixed"(req, res, { db }) {
    const figures = await dashboardFigures(db);
    res.set("X-Content-Signature", cheapSignature(figures));
    res.json(figures);
  },
}));

module.exports = router;

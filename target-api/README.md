# Target API (system under test)

An Express 5 e-commerce back end on PostgreSQL and Redis, instrumented with OpenTelemetry.
It is the application whose bottlenecks the project detects and diagnoses. It contains no
detection logic. Telemetry details are in [TELEMETRY.md](TELEMETRY.md).

## Domain API (Phase 3.5C-2)

| Operation | Route | Work (healthy implementation) |
|---|---|---|
| `users.list` | `GET /api/users?limit&offset` | page of users, each with an order count (index lookup per user) |
| `users.get` | `GET /api/users/:id` | user and their 10 latest orders |
| `products.list` | `GET /api/products?limit&offset&category` | page of products, summary columns only |
| `products.get` | `GET /api/products/:id` | full product with category and order count; cache-aside in Redis, TTL 60 s |
| `orders.list` | `GET /api/orders?user_id&limit` | a user's orders with item counts |
| `orders.create` | `POST /api/orders` `{user_id, items:[{product_id, quantity}]}` | places an order in one transaction |
| `search` | `GET /api/search?q&limit` | product name prefix search (index-ordered, stops at `limit`) |
| `reports` | `GET /api/reports?days` | sales by category over N days; heavy aggregation, cached in Redis for 60 s |
| `dashboard` | `GET /api/dashboard` | four summary queries in parallel (one cached) |

- Without `DATABASE_URL` the domain routes answer `503`.
- Reports use a fixed reference time (2026-11-01, the end of the seeded period), so their
  results do not change from day to day, and orders placed during a run do not shift them.
- The experiment endpoints (`/api/normal`, `/api/cpu`, `/api/payload`, `/api/query-sim`) and
  the calibration endpoints stay available.

### Implementation variants and injected faults (Phase 3.5C-3)

Every operation has a `healthy` implementation. Faults are realistic implementation
mistakes, and each has a `-fixed` variant: the change a developer would make. Where it
matters for a fair before/after comparison, the fixed variant returns byte-identical
responses to the faulty one (checked).

| Fault (diagnosis class) | Operations | Faulty implementation | Severity (default) | Fixed variant |
|---|---|---|---|---|
| `noindex` (DB_BOTTLENECK) | `orders.list`, `search` | the index the query relies on is dropped at startup (`orders_user_id_idx`, `products_name_lower_idx`) | none; grows with seed size | index recreated, `ANALYZE` |
| `nplus1` (N_PLUS_ONE) | `users.list`, `users.get` | one query, then one query per row, sequentially | rows per response (20 / 10) | one JOIN query |
| `payload` (LARGE_PAYLOAD) | `products.list`, `search` | pagination ignored, all columns (description, specs) | rows returned (2000 / 1000) | pagination + summary fields |
| `cache` (CACHE_OPPORTUNITY) | `products.get`, `reports` | cache key includes a per-request value, so every lookup misses | none | stable key, TTL 60 s |
| `cpu` (CPU_BOTTLENECK) | `products.get`, `dashboard` | a response signature derived with `pbkdf2Sync` on every request (blocks the event loop) | thousands of iterations (100) | ordinary SHA-256 hash |

Select variants at startup, as `name` or `name:severity`:

```sh
ABR_VARIANTS='{"users.list": "nplus1:50", "reports": "cache"}' scripts/lab-api.sh restart
```

- Operations not listed run `healthy`. Unknown operations or variants stop the server
  (`Refusing to start: ...`, also visible in `scripts/lab-api.sh logs`).
- Startup steps (dropping or recreating an index) finish before the first request. The
  lab reset before every run restores the seeded state.
- The variant is ground truth: it never appears in telemetry, logs or responses (tested).
- Queueing is not a code variant; it is a run setting (`PG_POOL_MAX` with high load).

#### Fault-activation checks (the 3.5C-3 gate)

```sh
npm run lab:fault-checks              # all faults, about 4 min, against a seeded lab (large)
npm run lab:fault-checks -- --only cpu
```

For every fault in every operation, the checker resets the lab, runs the faulty, fixed
and healthy variants, and checks the mechanism from telemetry and the database. It also
checks that effects grow with severity. Report:
`data/results/fault-checks/<timestamp>/report.md`. Result on 10 Nov 2026, large seed,
API on the host (medians of 8 requests):

| Fault / operation | Server ms faulty / fixed / healthy | Mechanism (faulty vs fixed) |
|---|---|---|
| nplus1 / users.list | 25.2 / 4.0 / 4.0 | 21 vs 1 queries per request |
| nplus1 / users.get | 23.4 / 15.9 / 13.0 | 12 vs 2 queries |
| noindex / orders.list | 53.3 / 6.1 / 6.5 | index absent; DB time 52.0 vs 4.9 ms |
| noindex / search | 28.7 / 3.2 / 4.0 | index absent; DB time 27.3 vs 2.0 ms |
| payload / products.list | 46.6 / 3.0 / 3.0 | 3.09 MB vs 1.7 kB |
| payload / search | 38.1 / 3.0 / 3.5 | 1.55 MB vs 1.2 kB |
| cache / products.get | 5.3 / 2.5 / 2.4 | DB query on every repeat vs none (a cheap query, so a weak effect) |
| cache / reports | 791.4 / 2.5 / 2.7 | DB query on every repeat vs none |
| cpu / products.get | 24.6 / 2.6 / 3.1 | same queries; +22 ms server time |
| cpu / dashboard | 31.0 / 9.6 / 10.9 | same queries; +21 ms server time |

Severity ladders: N+1 10/50/100 rows → 11/51/101 queries; payload 500/2000/5000 rows →
0.77/3.09/7.73 MB; CPU 20/100/300 → 6.8/23.5/63.1 ms. **All checks pass.**

## Database

`db/schema.sql` → `db/seed.sql` (deterministic, psql variables) → `db/constraints.sql`
(foreign keys and the healthy indexes, with fixed names). Build the lab template with
`scripts/lab-seed.sh small|medium|large` from the repository root.

| Scale | Users | Products | Orders | Order items | Size | Seed time* |
|---|---|---|---|---|---|---|
| small | 2,000 | 2,000 | 10,000 | ~30 k | 16 MB | 6 s |
| medium | 20,000 | 10,000 | 100,000 | ~300 k | | |
| large | 100,000 | 50,000 | 1,000,000 | ~3.0 M | 549 MB | 34–37 s |

\* measured on the project PC. The same scale always yields byte-identical tables; this was
checked by re-seeding and comparing table checksums.

## Running

| How | Command | Database / cache | Use |
|---|---|---|---|
| In the lab network (Docker) | `scripts/lab-api.sh up` | lab, container to container | **experiments** |
| On the host, lab profile | `npm run start:lab` | lab, through port forwarding | development, tests |
| On the host, cloud profile | `npm run start:env` | Neon + Redis Cloud | application use, demo |
| On the host, no database | `npm start` | none (domain routes give 503) | telemetry and calibration only |

The image (`Dockerfile`, Node 20, pinned by digest) is built by `lab-api.sh up`. Its
telemetry goes to the repository's `data/` folder through a bind mount.

## Tests

```sh
npm test                                   # no database: smoke, startup checks, variant registry
ABR_LAB_TESTS=1 npm run test:lab           # against a running, seeded lab (scripts/lab-seed.sh small or large)
```

The lab test places one order; run `scripts/lab-reset.sh` afterwards for a clean lab.

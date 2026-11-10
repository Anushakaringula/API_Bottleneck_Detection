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

### Implementation variants

Every operation has a `healthy` implementation. Phase 3.5C-3 adds faulty variants (with a
severity) and the remediated ones. The active variants are fixed at startup:

```sh
ABR_VARIANTS='{"users.list": "healthy"}' scripts/lab-api.sh restart
```

- Unknown operations or variants stop the server (`Refusing to start: ...`).
- The variant is ground truth: it never appears in telemetry, logs or responses. A test
  enforces this.

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

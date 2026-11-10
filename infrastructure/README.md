# Experiment lab (local PostgreSQL + Redis)

All measured experiments run against this lab, on the experiment machine. The cloud
services (Neon, Redis Cloud) are only for application use and the demo: their network
round trips (113–317 ms measured on 10 Nov 2026) are larger than the effects being
studied.

| Service | Image | Host address | Limits | Credentials (local only) |
|---|---|---|---|---|
| PostgreSQL | `postgres:16.15` (pinned by digest) | `127.0.0.1:5433` | 2 CPUs, 2 GB, 256 MB shm | user `lab`, password `lab` |
| Redis | `redis:7.4.12` (pinned by digest) | `127.0.0.1:6380` | 1 CPU, 512 MB | user `default`, password `labpass` |

The exact server versions are printed by `scripts/lab-status.sh` and recorded by the
lab probe.

## Commands (Git Bash or Linux shell, from the repository root)

```sh
cp .env.lab.example .env.lab     # once
scripts/lab-up.sh                # start, wait until healthy, print status
scripts/lab-reset.sh             # back to the seeded state (before every run)
scripts/lab-status.sh            # versions, seed description, row counts and checksums
scripts/lab-probe.sh             # GATE: from the lab network (where the API runs), p99 <= 2 ms
(cd target-api && npm run lab:probe)        # comparison: from the host shell (crosses port forwarding)
(cd target-api && npm run start:lab)        # target API against the lab
scripts/lab-down.sh              # stop (data kept); --purge also deletes the data volume
```

Docker Desktop must be running. On Windows its resources come from
`%USERPROFILE%\.wslconfig` (6 CPUs, 8 GB on the project PC).

## Databases

- `lab`: what the target API uses. `lab-reset.sh` drops it and recreates it as a copy
  of `lab_seed`, so every run starts from byte-identical data with a fresh table layout
  and the same planner statistics.
- `lab_seed`: the template. It is filled once by the seed step (Phase 3.5C-2), then
  `VACUUM ANALYZE` is run on it. It does not accept connections, so it cannot change by
  accident. The `lab_meta` table records which seed it holds.

`infrastructure/postgres/init/` runs only when the data volume is first created. After
changing it, run `scripts/lab-down.sh --purge` and then `scripts/lab-up.sh`.

## Configuration choices (`postgres/postgresql.conf`, `redis/redis.conf`)

| Setting | Why |
|---|---|
| Fixed container CPU and memory limits | Every run gets the same database machine |
| `autovacuum = off` | No background vacuum starting in the middle of a run; statistics are fixed after seeding |
| `jit = off` | JIT compilation adds run-to-run latency variance |
| `pg_stat_statements`, `track_io_timing` | Database-side ground truth (query counts and time, buffer hits vs reads), used to check that injected faults are active. Never a model feature. |
| Redis without persistence (`save ""`, `appendonly no`) | Every run starts from an empty cache; no disk writes in some runs but not others |
| Ports bound to `127.0.0.1` | The lab is not reachable from the network |

## Measured on the project PC (10 Nov 2026)

Docker Desktop 4.99 (WSL2), PostgreSQL 16.15, Redis 7.4.12, 2,000 samples each,
`data/results/lab-probe/`:

| Path | PostgreSQL `SELECT 1` p50 / p99 | Redis `PING` p50 / p99 |
|---|---|---|
| **Lab network** (container → container; where the target API runs) | **0.12 / 0.33 ms** | **0.12 / 0.32 ms** |
| Host shell → Docker (Windows port forwarding) | 0.53 / 1.38 ms (first run after start: 1.38 / 3.72) | 0.60 / 1.25 ms (first run: 1.28 / 2.93) |
| Cloud, for comparison (Neon / Redis Cloud) | 113–203 ms | 317 ms |

On Windows, Docker Desktop's port forwarding adds about 0.4–1.2 ms and jitter to every
call from the host. Every database query of a request pays it: an N+1 endpoint with 100
queries would gain about 100 ms. Therefore, in experiments **the target API runs inside the
lab network** (Phase 3.5C-2). The workload runner stays on the host, so the forwarding hop
lies only between client and server, and it is the same for every condition.
Reset reproducibility was checked: after changing data and Redis, `lab-reset.sh` restored
identical table checksums.

## Reset between runs

`lab-reset.sh` terminates connections to `lab`, so stop the target API first. The campaign
orchestrator (Phase 3.5D) does this for every run: reset, then start the API, warm up,
measure, stop.

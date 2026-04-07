# TaskForge Backend

A production-grade distributed backend system built to demonstrate expertise in FastAPI,
Celery, Redis, and PostgreSQL. The system is designed to handle large-scale data ingestion
(CSV uploads / high-volume API ingress) asynchronously — decoupling the API layer from
heavy processing work.

---

## Currently Implemented

### Authentication Module
- User registration with bcrypt password hashing
- Local login with JWT access + refresh token pair (cookie-based, HttpOnly)
- Google OAuth2 login via Authlib (OpenID Connect)
- Refresh token rotation — old token revoked on every refresh
- Token revocation using JTI hashing (SHA-256) stored in PostgreSQL
- Device, IP address, and user-agent tracking per refresh token
- Logout with cookie clearing and DB-level token invalidation
- `GET /auth/me/` — fetch authenticated user profile

### Jobs Module
- `job.jobs` table — tracks CSV upload lifecycle: pending → processing → completed/failed
- `job.job_data` table — stores each parsed CSV row as JSONB (one row per record)
- Pydantic schemas: `JobResponse` (post-upload) and `JobStatusResponse` (progress polling)
- Repository layer — async CRUD: `create_job`, `get_job_by_id`, `get_jobs_by_user`, `update_job`, `create_job_data`
- Service layer — file validation, disk storage, Celery dispatch, ownership checks
- `POST /jobs/upload/` — validates CSV, saves to disk, creates job record, dispatches Celery task
- `GET /jobs/{id}/` — returns full job status with progress counters and result data
- `GET /jobs/` — lists all jobs for the authenticated user
- `POST /jobs/{id}/retry/` — re-dispatches failed jobs only; pending/processing/completed are rejected
- Celery worker (`app.worker.tasks.process_csv`) — two-pass streaming: count rows then process
- Two-pass CSV processing — Pass 1 counts rows (no RAM usage), Pass 2 streams one chunk at a time
- Byte offset checkpointing — saves file position after each chunk; retry seeks instantly, no row scanning
- Chunk-based processing (CSV_CHUNK_SIZE=5000) — 50× fewer DB commits vs row-by-row
- Priority queue routing — files < 1 MB → `csv_priority`, larger → `csv_processing`
- Per-row error capture — failed rows stored in `error_detail` JSONB, not in `job_data`
- Soft time limit handling — saves progress and retries instead of silently failing
- Idempotency guard — completed jobs are never reprocessed on retry
- Structured logging at every stage: upload, dispatch, chunk commit, completion, failure

### Infrastructure
- Async SQLAlchemy 2.0 with asyncpg driver — non-blocking DB operations
- Sync psycopg2 engine in Celery worker — strict async/sync separation
- Alembic migrations — versioned schema management (`auth.users`, `auth.refresh_tokens`, `job.jobs`, `job.job_data`)
- Redis-backed rate limiting via SlowAPI (per-IP, per-route limits)
- Structured JSON logging via structlog with request_id tracing
- Dedicated security audit log (`logs/security.log`) with 1-year retention
- Request logging middleware — captures method, path, IP, duration (ms)
- Google OAuth integration via Authlib with session middleware
- Multi-stage Docker build (builder → slim runtime image)
- docker-compose with PostgreSQL 15, Redis 7, and a separate test database
- GitHub Actions CI: lint (ruff) → type check (mypy strict) → security audit (pip-audit) → tests → Docker build
- Pre-commit hooks: ruff format, ruff lint, mypy, YAML/TOML validation, private key detection

### Testing
- Unit tests: JWT token creation, password hashing/verification
- Unit tests: Celery task logic (job not found, already completed, success, resume, row failure, soft time limit)
- Unit tests: service layer validation (file type rejection, 404/403 ownership checks)
- Integration tests: register, login, logout, token refresh, `GET /me`, rate limiting, security edge cases
- Integration tests: CSV upload, job status polling, cross-user access (403), unauthenticated access (401)
- Integration tests: repository layer — create/get/update job, user isolation, job_data storage
- Savepoint-based transaction rollback — each test is fully isolated, no data pollution
- Celery patched in integration tests — no Redis connection required
- Separate test PostgreSQL instance (port 5433)
- In-memory Redis for rate limiter tests

---

## Problem Statement

Large CSV uploads or high-volume API ingress cause:
- API timeouts on synchronous processing
- Poor user experience with no progress feedback
- No retry mechanism when processing fails
- No audit trail for failures

**Solution:** Decouple ingestion from processing. The API accepts the upload immediately,
queues it, and returns a job ID. A Celery worker processes the data asynchronously, with
progress tracking, per-row error capture, and automatic retries.

---

## Architecture

```
                          ┌─────────────────────────────────┐
                          │           Client                │
                          └────────────┬────────────────────┘
                                       │ HTTP Request
                          ┌────────────▼────────────────────┐
                          │         FastAPI App              │
                          │  ┌──────────┐  ┌─────────────┐  │
                          │  │   Auth   │  │    Jobs     │  │
                          │  │  Module  │  │   Module    │  │
                          │  └──────────┘  └──────┬──────┘  │
                          │   Middleware Stack     │         │
                          │  (Rate limit, Logging) │         │
                          └────────┬───────────────┼─────────┘
                                   │               │ Enqueue task
                    ┌──────────────▼──┐    ┌───────▼──────────┐
                    │   PostgreSQL    │    │      Redis        │
                    │  ┌───────────┐  │    │  (Message Broker) │
                    │  │auth schema│  │    │  (Rate Limiter)   │
                    │  │job schema │  │    └───────┬──────────┘
                    │  │(JSONB)    │  │            │ Dequeue task
                    │  └───────────┘  │    ┌───────▼──────────┐
                    └─────────────────┘    │  Celery Worker   │
                             ▲             │  (CSV Processor) │
                             └─────────────┘
```

---

## System Components

| Component | Role |
|-----------|------|
| **FastAPI** | API layer — handles HTTP requests, auth, file ingestion, job dispatch |
| **PostgreSQL** | Primary datastore — user data, tokens, job state, processed rows (JSONB) |
| **Redis** | Dual role: Celery message broker + rate limiter backend |
| **Celery** | Async worker — processes CSV rows in chunks, handles retries, updates job progress |
| **Alembic** | Database migration management — versioned, reproducible schema changes |
| **structlog** | Structured JSON logging with request-scoped context variables |
| **slowapi** | Redis-backed per-IP rate limiting on sensitive endpoints |
| **Authlib** | Google OAuth2 / OpenID Connect integration |

---

## Data Flow

### Auth Flow (Implemented)
```
1. POST /auth/register/        → hash password → INSERT auth.users
2. POST /auth/login/           → verify password → issue JWT pair → SET cookies
3. GET  /auth/me/              → validate access token → SELECT auth.users
4. POST /auth/refresh/         → validate refresh token → rotate → SET new cookies
5. POST /auth/logout/          → revoke refresh token → CLEAR cookies
6. GET  /auth/google/login/    → redirect to Google OAuth consent
7. GET  /auth/google/callback/ → exchange code → upsert user → SET cookies
```

### Ingestion Flow (Implemented)
```
1. POST /jobs/upload/     → validate CSV → save to disk (uploads/{job_id}/) →
                            INSERT job.jobs (status=pending) →
                            route by file size: < 1MB → csv_priority, else → csv_processing →
                            process_csv.apply_async(job_id, queue=queue) → push to Redis → return job

2. Celery worker          → dequeue task → set status=processing →
                            Pass 1: count rows with csv.reader (no RAM spike) →
                            UPDATE total_rows →
                            Pass 2: seek to file_offset (instant resume) →
                            stream rows one chunk at a time (CSV_CHUNK_SIZE=5000) →
                            INSERT job.job_data rows (JSONB per row) →
                            UPDATE processed_rows, failed_rows, file_offset every chunk →
                            on row failure: append to error_detail, increment failed_rows →
                            on completion: status=completed, result_data (JSONB), completed_at

3. GET  /jobs/{id}/       → return job status + progress + result_data + error_detail
4. GET  /jobs/            → return all jobs for authenticated user
5. POST /jobs/{id}/retry/ → only allowed for failed jobs; pending/processing → 400

Retry / failure handling:
- SoftTimeLimitExceeded   → rollback session → save last_committed checkpoint → self.retry()
- Unexpected exception    → rollback session → save last_committed checkpoint → self.retry()
- Retry resume            → f.seek(job.file_offset) → instant jump, no row scanning
- Already completed       → skip (idempotency guard)
- Retry on pending job    → 400 "queued, worker will pick it up shortly"
- Retry on processing job → 400 "currently being processed, please wait"
```

---

## Tech Stack Justification

**FastAPI** — async-first, automatic OpenAPI docs, Pydantic v2 validation, native dependency
injection. Handles concurrent connections efficiently with a single worker process using
Python's asyncio event loop.

**PostgreSQL + JSONB** — relational integrity for structured data (users, tokens, job
metadata), JSONB for schema-flexible row storage (each CSV row stored as JSON). JSONB is
indexed and queryable — not just a blob.

**Redis** — low-latency message broker for Celery task queues. Also used as rate limiter
backend. Single service, two responsibilities.

**Celery** — battle-tested distributed task queue. Supports retries with exponential
backoff, task routing to named queues, soft/hard time limits, and result storage.
`task_acks_late=True` ensures tasks are not lost if a worker crashes mid-processing.

**SQLAlchemy 2.0 (async)** — modern async ORM with type-safe mapped columns. Uses asyncpg
driver for FastAPI and psycopg2 for Celery (sync) — strict separation prevents event loop
conflicts.

**Alembic** — version-controlled schema migrations. Supports autogenerate from SQLAlchemy
models. Every schema change is a versioned, reviewable, rollback-capable file.

---

## Security

| Feature | Implementation |
|---------|---------------|
| Password hashing | bcrypt via passlib |
| JWT signing | HS256, configurable expiry (15 min access, 7 day refresh) |
| Token revocation | JTI hashed with SHA-256, stored in `auth.refresh_tokens` |
| Token rotation | Old refresh token revoked on every `/auth/refresh/` call |
| Cookie security | HttpOnly, Secure (configurable), SameSite=lax |
| Rate limiting | Redis-backed SlowAPI — 3/min register, 5/min login, 10/min refresh, 5/min upload |
| OAuth2 | Google OpenID Connect — no password stored for OAuth users |
| Audit logging | Dedicated `logs/security.log` — all auth events logged with IP + device |
| Device tracking | IP, user-agent, and device stored per refresh token |
| Job ownership | Service layer enforces user_id check — 403 on cross-user access |
| Dependency scanning | pip-audit in CI pipeline — known CVEs documented and tracked |

---

## Worker Configuration (via `.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `CSV_CHUNK_SIZE` | 5000 | Rows committed per DB batch (50× fewer commits vs 100) |
| `CSV_TASK_SOFT_TIME_LIMIT` | 3600 | Seconds before SoftTimeLimitExceeded (1 hr for large files) |
| `CSV_TASK_MAX_RETRIES` | 3 | Max retry attempts per task |
| `CSV_TASK_RETRY_DELAY` | 60 | Seconds between retries |
| `UPLOAD_DIR` | uploads | Directory for uploaded CSV files |

---

## Scalability Strategy

**Horizontal worker scaling:** Celery workers are stateless. Add more worker containers
pointing at the same Redis broker to increase processing throughput. Each worker processes
one CSV task at a time (`worker_prefetch_multiplier=1`) to prevent head-of-line blocking.

**Priority queue routing:** Two queues — `csv_priority` (files < 1 MB, fast jobs) and
`csv_processing` (large files). Workers consume `csv_priority` first, so small jobs are
never blocked behind a 2.5M row job. Workers run with `-Q csv_priority,csv_processing`.

**Queue isolation:** CSV processing tasks route to dedicated queues. Future task types
(email, exports, webhooks) get separate queues — no one workload starves another.

**Async API layer:** FastAPI with asyncpg handles thousands of concurrent connections on
a single process. DB operations never block the event loop.

**Schema separation:** `auth` and `job` PostgreSQL schemas are logically isolated. Each
module owns its schema — no cross-module table joins in the hot path.

**Byte offset checkpointing:** After each chunk commit, the worker saves the exact file
byte position (`file_offset`). On retry, `f.seek(file_offset)` jumps instantly to the
resume point — no scanning through already-committed rows.

**Streaming processing:** CSV is never fully loaded into RAM. Pass 1 counts rows with a
buffered line scan (~8KB buffer). Pass 2 streams one chunk at a time (~1MB per worker).
Three workers processing simultaneously use ~3MB total, not hundreds of MB.

**Known limitation — idle workers on uneven workloads:** Each job is processed by a single
worker. If two jobs are queued (2.5M rows and 250k rows), Worker 1 takes the large job and
Worker 2 takes the small job. After Worker 2 finishes, it sits idle until a new job arrives
— it cannot help Worker 1 with the remaining rows. The production solution is fan-out
processing: split large files into N chunk tasks at upload time, one Celery task per chunk,
so multiple workers collaborate on the same file. A coordinator task marks the job complete
when all chunks finish. This is a deliberate scope decision — correctness and reliability
were prioritised first.

---

## Failure Handling

### Currently in place
- Refresh token revocation on logout prevents reuse after session end
- Database rollback on integrity errors (duplicate email/username)
- JWT validation with type checking (access vs refresh token type enforcement)
- Rate limiting prevents brute-force on login/register and upload spam
- `task_acks_late=True` — task re-queued automatically if worker crashes
- `SoftTimeLimitExceeded` caught — session rolled back, last_committed checkpoint saved, task retried
- Per-row error capture — failed rows in `error_detail` JSONB, task continues
- Idempotent retries — `status == "completed"` guard prevents double processing
- Retry blocked on pending/processing jobs — prevents duplicate task dispatch race condition
- Byte offset resume — retry seeks to exact file position, never rescans committed rows
- Max 3 retries with 60s delay — prevents infinite retry loops

---

## Deployment

### Local Development
```bash
docker-compose up --build
# FastAPI:  http://localhost:8000
# Docs:     http://localhost:8000/docs
```

### Running the Celery Worker
```bash
# Consumes csv_priority first, then csv_processing
celery -A app.worker.celery_app worker --loglevel=info -Q csv_priority,csv_processing
```

### Database Migrations
```bash
# Create a new migration after model changes
alembic revision --autogenerate -m "description"

# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1
```

### Docker
Multi-stage Dockerfile:
- **Builder stage:** installs build tools, compiles Python wheels from requirements
- **Runtime stage:** `python:3.11-slim`, copies pre-built wheels — minimal final image

### Environment
All secrets and config loaded from `.env` via Pydantic `BaseSettings`. Required vars:
`SECRET_KEY`, `POSTGRES_*`, `REDIS_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `UPLOAD_DIR`.

### Production Checklist
- [ ] Set `COOKIE_SECURE=true` and `DEBUG=false`
- [ ] Set `CORS_ORIGINS` to exact frontend domain (not `*`)
- [ ] Rotate `SECRET_KEY` and store in a secrets manager (not `.env`)
- [ ] Configure `pool_size` and `max_overflow` on the SQLAlchemy engine
- [ ] Enable `celery_worker` service in docker-compose (or deploy as separate container)
- [ ] Mount shared volume between `app` and `celery_worker` for `UPLOAD_DIR`
- [ ] Set up log aggregation (Datadog, Loki, CloudWatch)

---

## Roadmap

| Feature | Status |
|---------|--------|
| User registration + login (local) | Done |
| Google OAuth2 | Done |
| JWT rotation + revocation | Done |
| Alembic migrations | Done |
| Job DB models (`job.jobs`, `job.job_data`) | Done |
| Job Pydantic schemas | Done |
| Job repository layer | Done |
| Job service layer | Done |
| CSV upload endpoint (`POST /jobs/upload/`) | Done |
| Job status endpoint (`GET /jobs/{id}/`) | Done |
| Job list endpoint (`GET /jobs/`) | Done |
| Celery worker + CSV task | Done |
| Checkpoint-based retry resume | Done |
| Per-row error capture | Done |
| docker-compose celery_worker service + shared volume | Planned |
| Prometheus `/metrics` endpoint | Planned |
| OpenTelemetry distributed tracing | Planned |
| Celery Flower monitoring UI | Planned |
| Dead-letter queue for failed tasks | Planned |
| WebSocket / SSE progress streaming | Planned |

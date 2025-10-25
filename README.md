## Redis Cache

- Start local Redis with `docker run --rm -p 6379:6379 redis:7`.
- `.env.dev` uses `REDIS_URL` (defaults to `redis://127.0.0.1:6379/1`).
- `.env.prod` ships with a placeholder `REDIS_URL=redis://127.0.0.1:6379/0`.
- Tests and CI stay on LocMem; no Redis service required there.

## API Tooling

- Generate OpenAPI + Postman via `make postman` (or `scripts/generate_postman.ps1`).
- Requires Python 3.13+ and Node.js >=16 (for `npx openapi-to-postmanv2`).

## Background Jobs (RQ)

- **Redis:** `docker run --rm -p 6379:6379 redis:7` (or point `RQ_REDIS_URL` at your instance).
- **Windows / PowerShell:**

  ```powershell
  .\.venv\Scripts\activate
  python manage.py run_rq_worker
  ```

- **macOS / Linux:**

  ```bash
  source .venv/bin/activate
  python manage.py run_rq_worker
  ```

- **Alternative:** `python -m rq worker transcode default --url %RQ_REDIS_URL%`
- **API:** Trigger transcoding via the existing `POST /api/videos/<id>/transcode/` endpoint; status can be inspected via the admin or by querying the cache.

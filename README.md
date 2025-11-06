## Redis Cache

- Start local Redis with `docker run --rm -p 6379:6379 redis:7`.
- `.env.dev` keeps using `REDIS_URL` (default `redis://127.0.0.1:6379/1`).
- `.env.prod` ships with the placeholder `REDIS_URL=redis://127.0.0.1:6379/0`.
- Tests and CI stay on LocMem; no Redis service required.

## API Tooling

- Generate OpenAPI + Postman via `make postman` (or `scripts/generate_postman.ps1`).
- Requires Python 3.13+ and Node.js >=16 (for `npx openapi-to-postmanv2`).

## Background Worker (django-rq)

- **Environment variables**
  - `RQ_URL` (default `redis://127.0.0.1:6379/0`)
  - `RQ_QUEUE_TRANSCODE` (default `transcode`, empty value enables inline fallback)
- **Start Redis locally:** `docker run --rm -p 6379:6379 redis:7`
- **Start worker:**

  ```bash
  python manage.py rqworker_transcode
  ```

  Use `--burst` to process queued jobs once and exit. The command raises a clear
  error when the queue name is missing or `django_rq` is unavailable.
- **Inline fallback:** If Redis cannot be reached the backend logs
  `RQ queue not available; running inline transcode` and executes the
  transcode in-process.
- **Debug tooling (DEBUG=True)**
  - `/admin/rq/` exposes the django-rq dashboard.
  - `GET /api/_debug/queue` reports connectivity and queue length (always
    served with `Cache-Control: no-cache`).

Trigger new renditions via `POST /api/video/<public_id>/transcode/`.
Inspect the current HLS state with `GET /api/video/<public_id>/health`.

### Deployment snippets

**systemd worker (Linux)**

```
[Unit]
Description=RQ worker (transcode)
After=network.target

[Service]
WorkingDirectory=/srv/videoflix_backend
Environment="RQ_URL=redis://127.0.0.1:6379/0" "RQ_QUEUE_TRANSCODE=transcode"
ExecStart=/srv/videoflix_backend/.venv/bin/python manage.py rqworker_transcode
Restart=always

[Install]
WantedBy=multi-user.target
```

**docker-compose extract**

```
services:
  redis:
    image: redis:7
    ports: ["6379:6379"]

  rqworker:
    build: .
    command: python manage.py rqworker_transcode
    environment:
      - RQ_URL=redis://redis:6379/0
      - RQ_QUEUE_TRANSCODE=transcode
    depends_on:
      - redis
```

## Transcoding

- Creating or updating a video source automatically schedules 480p and 720p renditions.
- When Redis/django-rq are available the work is enqueued; otherwise it runs inline.
- A cache-based debounce (10 seconds) prevents duplicate transcode kicks on rapid saves.

## Self-healing HLS

- HLS manifests and segments are served straight from `MEDIA_ROOT/hls/<real>/<res>`; database rows are recreated or refreshed during the same request without blocking the response.
- Missing files are ignored gracefully, while successful self-heal runs upsert the manifest text and segment binaries for future fallbacks.
- Reindex existing material in bulk with `python manage.py index_renditions` (supports `--real`, `--public`, `--res`, `--all`) and review the per-run summary (`ok/updated/missing`).


# Deployment Guide

## Environment Variables

| Name | Purpose | Example |
| ---- | ------- | ------- |
| `ENV` | Runtime profile switch used by settings/autotranscode. | `prod` |
| `DEBUG` | Enables Django debug mode (must be `0` in production). | `0` |
| `SECRET_KEY` | Django secret; set via secret manager or env (never commit!). | `change-me` |
| `ALLOWED_HOSTS` | Comma-separated list of hostnames served by Django. | `videoflix.api.sascha-gerspach.tld,localhost` |
| `CORS_ALLOWED_ORIGINS` | Space-separated origins allowed to call the API. | `https://videoflix.webapp.local https://videoflix.api.sascha-gerspach.tld` |
| `PUBLIC_MEDIA_BASE` | Base URL used to generate absolute media URLs when no request object is present. | `https://videoflix.api.sascha-gerspach.tld` |
| `MEDIA_ROOT` | Absolute path for uploaded sources/HLS/thumbs. | `/srv/videoflix/media` |
| `MEDIA_URL` | Public URL prefix for MEDIA_ROOT. | `/media/` |
| `RQ_REDIS_URL` | Redis connection string for django-rq (worker + web). | `redis://127.0.0.1:6379/1` |
| `RQ_QUEUE_TRANSCODE` | Queue name for the transcode worker. | `transcode` |
| `ACCESS_COOKIE_NAME` | Name of the HttpOnly cookie containing the access token. | `access_token` |

## Reverse Proxy (Nginx)

```nginx
server {
  listen 443 ssl;
  server_name videoflix.api.sascha-gerspach.tld;

  ssl_certificate     /etc/letsencrypt/live/videoflix.api.sascha-gerspach.tld/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/videoflix.api.sascha-gerspach.tld/privkey.pem;

  # Proxy all Django routes to Gunicorn/Uvicorn listening on localhost:8000
  location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-Proto https;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 120s;
  }

  # Static media: HLS segments + thumbnails
  location /media/ {
    alias /srv/videoflix/media/;
    expires 7d;
    add_header Cache-Control "public, max-age=604800";
    add_header Access-Control-Allow-Origin "https://videoflix.webapp.local";
    add_header Access-Control-Allow-Credentials "true";
  }
}
```

## Django Cookies & CORS (Production)

Add the following to `core/settings.py` (or equivalent prod settings module):

```python
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = "None"
CSRF_TRUSTED_ORIGINS = ["https://videoflix.api.sascha-gerspach.tld"]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    "https://videoflix.webapp.local",
]
```

Ensure `ACCESS_COOKIE_NAME` matches the frontend cookie name and that `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` is set when running behind Nginx.

## Worker & Queues

Windows-friendly worker (auto-selects `rq.SimpleWorker`):

```powershell
python manage.py rqworker_transcode
```

Linux / standard django-rq worker:

```bash
python manage.py rqworker transcode
```

Both commands need Redis reachable via `RQ_REDIS_URL`. Monitor worker logs for the “Starting RQ worker … Worker class: …” message to confirm the correct class is used.

## Regular Maintenance

`media_maintenance` consolidates scan/heal/enqueue/prune tasks. Examples:

```bash
# nightly drift detection
python manage.py media_maintenance --scan --json

# heal stub manifests, enqueue missing renditions
python manage.py media_maintenance --heal --enqueue-missing --res 480p --res 720p --res 1080p

# prune orphan HLS folders (requires --confirm)
python manage.py media_maintenance --prune-orphans --confirm
```

Cron example (run daily at 03:00 UTC):

```
0 3 * * * cd /srv/videoflix && /usr/bin/python manage.py media_maintenance --scan --enqueue-missing --heal >> /var/log/media_maintenance.log 2>&1
```

## Pre Go-Live Checklist

1. `python manage.py migrate`
2. `python manage.py createsuperuser`
3. Configure HTTPS (certbot/Let’s Encrypt) for `videoflix.api.sascha-gerspach.tld`
4. Verify `DEBUG=0`, `ALLOWED_HOSTS`, CORS settings, cookie security flags
5. Run automated tests: `python -m pytest -q`
6. Launch worker: `python manage.py rqworker_transcode`
7. Warm up media: `python manage.py media_maintenance --scan`
8. Smoke test API & healthcheck: `curl https://videoflix.api.sascha-gerspach.tld/healthz/`

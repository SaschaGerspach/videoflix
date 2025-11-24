# Videoflix Backend - Video Streaming API

This repository contains the backend for **Videoflix**, a small video streaming platform with:

- User registration, activation, login/logout
- Password reset via email
- Video management (upload, transcoding)
- HLS streaming (M3U8 manifest + TS segments)

The code is based on **Django / Django REST Framework** and is fully launched through **Docker Compose**.

---

## Tech Stack

- **Backend:** Django 5 + Django REST Framework
- **Database:** PostgreSQL
- **Queue / Worker:** Redis + RQ worker (for transcoding jobs)
- **Web server / reverse proxy:** Nginx + Gunicorn
- **Docs / API spec:** drf-spectacular (OpenAPI 3), Postman collection

Everything is launched as a stack via `compose.yml`.

---

## Requirements

- Docker & Docker Compose (Docker Desktop on Windows)
- PowerShell (for the start/stop scripts on Windows)

Optional (only for API schema/collection generation):

- `make`
- Node.js + `npx` (for `openapi-to-postmanv2`)

---

## Start & Stop

### Quickstart (local)

1. Clone the repo.
2. Create `.env` from `.env.example` and adjust the values.
3. Run `.\start_videoflix.ps1`.
4. Open the API at http://127.0.0.1:8000/api/.

### 1. Clone the repository

```bash
git clone <URL>
cd videoflix_backend
```

### 2. Environment

For local development the project uses a `.env` file in the repo root. An `.env.example` is included with all required variables as placeholders. Create your own `.env` and adjust it:

```bash
cp .env.example .env
```

The example file is configured for a production/staging environment (`ENV=prod`, `DEBUG=False`). For local work you can switch to `ENV=dev` (or `DEBUG=True`) and set `EMAIL_BACKEND=console` or `ENV=console`.

Fill the values you need:

- `SECRET_KEY` - generated Django secret key
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` - only required if you want to test activation/password reset via SMTP
- `FRONTEND_BASE_URL` - e.g. http://127.0.0.1:5500
- `DEV_FRONTEND_ORIGIN` - default: http://127.0.0.1:5500

Note: if you do not configure SMTP, activation and password reset still return valid responses; emails are caught silently for local development.

#### 2.1 Show the active .env (optional)

If you want to inspect the loaded configuration inside the container:

```bash
docker compose -f compose.yml exec web env | sort
```

#### 2.2 Test email sending locally (optional)

You can use Mailtrap, a Gmail app password, or a local dev SMTP such as MailHog. Example MailHog settings:

```bash
EMAIL_HOST=mailhog
EMAIL_PORT=1025
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
```

#### 2.3 Print activation and reset links in the console (optional)

If you prefer not to use SMTP, set in `.env`:

```bash
ENV=console
```

Registration and password reset URLs will then be printed to the web container logs, viewable via:

```bash
docker compose -f compose.yml logs -f web
```

### 3. Start the stack (recommended approach on Windows)

```bash
.\start_videoflix.ps1
```

The script runs internally:

- `docker compose -f compose.yml down --remove-orphans`
- (optional) `docker compose -f compose.yml build`
- `docker compose -f compose.yml up -d`
- waits until `postgres`, `redis` and `web` are healthy
- `docker compose -f compose.yml exec web python manage.py migrate --noinput`

After that the backend is available at:

- API: http://127.0.0.1:8000/api/
- Admin: http://127.0.0.1:8000/admin/

### 4. Stop the stack

```bash
.\stop_videoflix.ps1
```

or manually:

```bash
docker compose -f compose.yml down --remove-orphans
```

## Authentication & User Flow

### Registration & activation

#### 1. Registration

```bash
POST /api/register/
Content-Type: application/json

{
  "email": "demo@example.com",
  "password": "Admin123!",
  "confirmed_password": "Admin123!"
}
```

Response (201):

```bash
{
  "user": {
    "id": 13,
    "email": "demo@example.com"
  },
  "uidb64": "MTM",
  "token": "czb8ae-..."
}
```

#### 2. Activation

The activation link in the email points to:

```bash
GET /api/activate/<uidb64>/<token>/
```

Example:

```bash
GET /api/activate/MTM/czb8ae-55ad3af1ff5d9f437757d2a225f23845/
```

- 200: `{"message": "Account successfully activated."}`
- 400: `{"errors": ["Invalid or expired activation link."]}`

#### 3. Guest login (demo account)

A preinstalled demo user is available for review:

```bash
Email: demo@videoflix.local
Password: Demo123!
```

The demo account is created automatically by the database migration that runs in the start script and requires no registration, email activation, or SMTP setup.

## Login / Logout / Token Refresh

### Login

```bash
POST /api/login/
{
  "email": "demo@videoflix.local",
  "password": "Demo123!"
}
```

Response: `Login successful` + user data; the session is managed via cookies.

---

### Logout

```bash
POST /api/logout/
```

### Access Token Refresh

```bash
POST /api/token/refresh/
```

Requires a valid refresh cookie and returns a new access token.

## Password Reset

### 1. Request reset

```bash
POST /api/password_reset/
{
  "email": "demo@example.com"
}
```

Response (always 200, even if the email does not exist):

```bash
{ "detail": "If this email exists, a password reset link has been sent." }
```

### 2. Set a new password

The link in the email points to the frontend, which then:

```bash
POST /api/password_confirm/<uidb64>/<token>/
{
  "new_password": "NewPassword123!",
  "confirm_password": "NewPassword123!"
}
```

- 200: `{"detail": "Password updated."}`
- 400: e.g. `{"errors": {"confirm_password": ["Passwords do not match."]}}`

## Video API & HLS

### List all published videos

```bash
GET /api/video/?ready_only=true&order=-updated_at
```

Response (example):

```bash
[
  {
    "id": 1,
    "created_at": "2025-11-13T12:57:07.992501Z",
    "title": "Demo Video",
    "description": "",
    "thumbnail_url": "http://localhost:8000/media/thumbs/1/default.jpg",
    "category": "drama"
  }
]
```

---

### Video Upload (Management Command)

You can import a local video into the container in two steps:

#### 1. Copy the file into the container

```bash
# Example: copy from local Windows path into the web container
docker cp "C:\Users\Anwender\Videos\final_hq.mp4" videoflix_backend-web-1:/app/media/final_hq.mp4
```

Note: The container name (videoflix_backend-web-1) may vary depending on your system.
You can check the exact name using:

```bash
docker compose -f compose.yml ps web
```

#### 2. Run the upload command inside the container

PowerShell (Windows):

```bash
docker compose -f compose.yml exec web python manage.py upload_video "/app/media/final_hq.mp4" --owner demo@videoflix.local --title "Demo Video" --publish --move --json

```

Alternativ (Bash / Linux / macOS):

```bash
docker compose -f compose.yml exec web \
  python manage.py upload_video /app/media/final_hq.mp4 \
  --owner demo@videoflix.local \
  --title "Demo Video" \
  --publish \
  --move \
  --json
```

The command:

- creates a Video record
- moves the source file inside the container (because of --move)
- triggers automatic transcodes (480p/720p/1080p, depending on policy)
- generates a thumbnail file.

---

### Transcoding (optional manual trigger)

```bash
POST /api/videos/{video_id}/transcode/
```

Response:

```bash
{
  "detail": "Transcode accepted",
  "video_id": 1
}
```

### HLS Manifest

```bash
GET /api/{movie_id}/{resolution}/index.m3u8
```

Example:

```bash
GET /api/1/480p/index.m3u8
```

Response: M3U8 HLS master playlist (Content-Type: application/vnd.apple.mpegurl).

### HLS Segment

```bash
GET /api/{movie_id}/{resolution}/{segment}/
```

Example:

```bash
GET /api/1/480p/000.ts
```

- 200 + TS segment `(Content-Type: video/MP2T)`
- 404 + `{"errors": {"non_field_errors": ["Video segment not found."]}}` when the segment does not exist

## API Documentation & Postman

### OpenAPI Schema

- `schema.yaml` - OpenAPI 3 (YAML)
- `schema.json` - OpenAPI 3 (JSON)

Both are generated with `drf-spectacular`.

Generate schema:

```bash
make schema
```

### Postman Collection

- Pre-built collection in the repo: `videoflix_postman_collection.json`
  - `{{base_url}} = http://127.0.0.1:8000/api`

Optional: generate a new collection from the OpenAPI schema:

```bash
make postman
```

Result: `postman/collection.json`, importable in Postman.

### Tests & Coverage

Run tests inside the container:

```bash
docker compose -f compose.yml exec web pytest -q
```

- Coverage threshold: 80%
- Currently: ~88% total coverage (focused on videos, jobs, and domain tests)

### Misc / Notes

- **Media & HLS files** are generated at runtime under `media/` and are not part of the repo.
- #### SMTP / Email:
  - Activation and reset emails use the configured email backend.
  - For production delivery the SMTP backend must be configured correctly in .env.
- #### Redis & Worker:
  - Redis serves as cache and queue backend.
  - The RQ worker processes transcoding jobs; it starts automatically with the stack.

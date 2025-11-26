#!/usr/bin/env sh
set -e

COMPOSE_FILE="compose.yml"

echo "Stopping old stack (if any)…"
docker compose -f "$COMPOSE_FILE" down --remove-orphans || true

echo "Building images (this may take a while on first run)…"
docker compose -f "$COMPOSE_FILE" build

echo "Starting containers in background…"
docker compose -f "$COMPOSE_FILE" up -d

echo "Running Django migrations…"
docker compose -f "$COMPOSE_FILE" exec web python manage.py migrate --noinput

echo "Collecting static files…"
docker compose -f "$COMPOSE_FILE" exec web python manage.py collectstatic --noinput

echo ""
echo "Stack is up. You can open:"
echo "  - API:   http://127.0.0.1:8000/api/"
echo "  - Admin: http://127.0.0.1:8000/admin/"

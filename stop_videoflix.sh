#!/usr/bin/env sh
set -e

COMPOSE_FILE="compose.yml"

echo "Stopping Videoflix stack…"
docker compose -f "$COMPOSE_FILE" down --remove-orphans

echo "Videoflix stack stopped."

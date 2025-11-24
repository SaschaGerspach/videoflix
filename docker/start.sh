#!/usr/bin/env bash
set -euo pipefail

# Example cron entry for nightly media maintenance.
# This writes a cron file (disabled by default) that can be enabled if desired.
CRON_FILE=/etc/cron.d/videoflix-media-maintenance.example
if [[ ! -f "${CRON_FILE}" ]]; then
cat <<'EOF' >"${CRON_FILE}"
# Run Django media maintenance every day at 03:00
0 3 * * * root cd /app && /usr/bin/env PATH="$PATH" python manage.py media_maintenance --scan --enqueue-missing --heal >> /var/log/media_maintenance.log 2>&1
EOF
chmod 0644 "${CRON_FILE}"
fi

exec "$@"

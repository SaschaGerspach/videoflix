#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

# ---- Auto-ENV-Auswahl für lokale Dev & CI ----
# Lädt .env.ci wenn Pytest läuft,
# sonst .env.dev (default), und .env.prod wenn ENV=prod gesetzt ist.
try:
    from dotenv import load_dotenv  # type: ignore
    BASE_DIR = Path(__file__).resolve().parent
    # Pytest-Run?
    if "PYTEST_CURRENT_TEST" in os.environ and (BASE_DIR / ".env.ci").exists():
        load_dotenv(BASE_DIR / ".env.ci")
    else:
        env_name = os.environ.get("ENV", "").lower()
        if env_name == "prod" and (BASE_DIR / ".env.prod").exists():
            load_dotenv(BASE_DIR / ".env.prod")
        elif (BASE_DIR / ".env.dev").exists():
            load_dotenv(BASE_DIR / ".env.dev")
except Exception:
    # Fallback: Wenn python-dotenv nicht installiert ist,
    # läuft alles mit System-Umgebungsvariablen weiter.
    pass
# ---- Ende Auto-ENV-Auswahl ----


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

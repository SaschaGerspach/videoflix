FROM python:3.13-slim

# Keep logs unbuffered and bytecode out of bind mounts.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps needed for psycopg, Pillow, ffmpeg jobs, and healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        libmagic1 \
        libpq-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the Django project.
COPY . .

# Ensure media/static folders exist (media is mounted as a volume in compose).
RUN mkdir -p /app/media /app/staticfiles

# Optional minimaler Sanity-Check
RUN python -m compileall .


# Collect static assets here later if/when a CDN or nginx serves them directly.
# RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Default CMD keeps the image prod-ready; docker-compose overrides for workers.
CMD ["gunicorn", "core.wsgi:application", "-b", "0.0.0.0:8000", "--workers", "3", "--timeout", "90"]

FROM python:3.12-slim

# ffmpeg renders video (Phase 7). curl backs the HEALTHCHECK below (and is
# handy for manual debugging). fonts-dejavu-core covers Latin on-screen
# text; fonts-noto-core adds Devanagari (Hindi) glyph coverage so a hi-IN
# project's on-screen text doesn't render as blank boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    fonts-dejavu-core \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Phase 15: run as a non-root user instead of root, standard hardening for
# anything exposed beyond your own laptop. /app/assets and /app/output are
# normally hidden by the bind mounts docker-compose.yml adds over them —
# see that file's comments for the one-time `chown` a Linux production
# host needs so this user can still write into those host folders. Created
# here too so the image is still correct if ever run standalone, without
# this project's compose setup (no bind mounts to hide them).
RUN mkdir -p /app/assets /app/output \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Applies as-is to the `backend` service (a real HTTP check). The `worker`
# service shares this image but isn't a web server, so docker-compose.yml
# overrides this with a celery-specific check for that service — Compose's
# per-service `healthcheck:` fully replaces this one, it doesn't merge.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

#!/usr/bin/env bash
set -e

# RUN_MIGRATIONS=false skips this (set on the worker service in
# docker-compose.yml, Phase 11) — the backend container already runs
# `alembic upgrade head` on its own startup, so having the worker container
# race it to apply the same migrations concurrently on every restart is
# pointless risk for no benefit. Defaults to running them (every other
# service that uses this entrypoint has no reason to skip it).
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] Running database migrations..."
    alembic upgrade head
else
    echo "[entrypoint] RUN_MIGRATIONS=false, skipping migrations."
fi

echo "[entrypoint] Starting: $*"
exec "$@"

#!/bin/bash

# =============================================================================
# Music Grabber Entrypoint
# =============================================================================
# Supports PUID/PGID environment variables for running as a specific user,
# similar to Linuxserver.io containers and the *arr stack.
# =============================================================================

PUID=${PUID:-0}
PGID=${PGID:-0}
LISTEN_ADDR=${LISTEN_ADDR:-0.0.0.0}
LISTEN_PORT=${LISTEN_PORT:-8080}

echo ""
echo "=========================================="
echo "  Music Grabber is starting..."
echo "  Listening on ${LISTEN_ADDR}:${LISTEN_PORT} (map host port in docker-compose as needed)"

# Only do user/group setup if not running as root (PUID/PGID specified)
if [ "$PUID" != "0" ] || [ "$PGID" != "0" ]; then
    echo "  Running as UID: $PUID / GID: $PGID"

    # Create group if it doesn't exist
    if ! getent group musicgrabber > /dev/null 2>&1; then
        groupadd -g "$PGID" musicgrabber
    fi

    # Create user if it doesn't exist
    if ! getent passwd musicgrabber > /dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -d /app -s /bin/bash musicgrabber
    fi

    # Ensure ownership of key directories
    chown -R "$PUID:$PGID" /app /data 2>/dev/null || true

    # Run as the specified user
    echo "=========================================="
    echo ""
    exec gosu musicgrabber uvicorn app:app --host "$LISTEN_ADDR" --port "$LISTEN_PORT"
else
    echo "  Running as root (set PUID/PGID for custom user)"
    echo "=========================================="
    echo ""
    exec uvicorn app:app --host "$LISTEN_ADDR" --port "$LISTEN_PORT"
fi

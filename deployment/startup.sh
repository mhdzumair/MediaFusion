#!/bin/bash
set -e

# psql accepts postgresql:// URIs (strip +asyncpg for SQLAlchemy format)
# Support both POSTGRES_URI and postgres_uri (Pydantic accepts either casing)
PSQL_URI="${POSTGRES_URI:-${postgres_uri:-postgresql://mediafusion:mediafusion@localhost:5432/mediafusion}}"
PSQL_URI="${PSQL_URI/postgresql+asyncpg/postgresql}"
# Poolers (Neon, Supabase) require SSL for non-localhost; append sslmode if not present
if [[ "$PSQL_URI" != *"sslmode="* ]] && [[ "$PSQL_URI" != *"@localhost"* ]] && [[ "$PSQL_URI" != *"@127.0.0.1"* ]]; then
    [[ "$PSQL_URI" == *"?"* ]] && PSQL_URI="${PSQL_URI}&sslmode=require" || PSQL_URI="${PSQL_URI}?sslmode=require"
fi
# System DB URI (preserve query string for sslmode)
PSQL_BASE="${PSQL_URI%%\?*}"
PSQL_QUERY="${PSQL_URI#*\?}"
PSQL_URI_SYSTEM="${PSQL_BASE%/*}/postgres"
[[ "$PSQL_QUERY" != "$PSQL_URI" ]] && PSQL_URI_SYSTEM="${PSQL_URI_SYSTEM}?${PSQL_QUERY}"

wait_for_postgres() {
    echo "Waiting for PostgreSQL..."
    for i in $(seq 1 30); do
        psql "$PSQL_URI_SYSTEM" -c '\q' 2>/dev/null && echo "PostgreSQL is ready!" && return 0
        echo "Attempt $i/30: waiting..."
        sleep 2
    done
    echo "ERROR: PostgreSQL did not become ready in time"
    exit 1
}

create_database_if_not_exists() {
    DB_NAME="${PSQL_URI##*/}"
    DB_NAME="${DB_NAME%%\?*}"
    echo "Checking if database '$DB_NAME' exists..."
    if psql "$PSQL_URI_SYSTEM" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" 2>/dev/null | grep -q 1; then
        echo "Database '$DB_NAME' already exists."
    else
        echo "Creating database '$DB_NAME'..."
        psql "$PSQL_URI_SYSTEM" -c "CREATE DATABASE $DB_NAME"
    fi
}

create_extensions() {
    echo "Creating extensions..."
    psql "$PSQL_URI" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" 2>/dev/null || true
}

handle_migration_bridge() {
    echo "Checking for migration upgrade bridge..."
    CURRENT_REV=$(psql "$PSQL_URI" -tAc "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null | tr -d '[:space:]' || true)
    [ -z "$CURRENT_REV" ] && echo "No existing alembic version." && return 0

    BASE_REV="050497c15ecc"
    HEAD_REV="f1e2d3c4b5a6"
    BETA1_REVISIONS="4829e203ecaf c63392160ce7 bf75239e668e 7f6e3631b327 d1a2b3c4d5e6 8d55e5e54b6a 55db6bd4aab2 64ab9417af09 32962aacd8dc 5633887f53ad a1b2c3d4e5f6 b2c3d4e5f6a7 e7e460f99493"
    OLD_DEV_REVISIONS="a1b2c3d4e5f6 17334c3ffa57 b6974f19eb9a 641f2ff525e4 302b751510cd a675cdb2e88a c7a8b9d0e1f2 53827f147dc2 d8e9f0a1b2c3 1baf610e47f4 95f08ba65a00"

    for rev in $BETA1_REVISIONS; do
        if [ "$CURRENT_REV" = "$rev" ]; then
            echo "Detected v5.0.0-beta.1 (revision: $CURRENT_REV), updating to $BASE_REV..."
            psql "$PSQL_URI" -c "UPDATE alembic_version SET version_num = '$BASE_REV'"
            return 0
        fi
    done
    for rev in $OLD_DEV_REVISIONS; do
        if [ "$CURRENT_REV" = "$rev" ]; then
            echo "Detected pre-consolidation revision ($CURRENT_REV), updating to $HEAD_REV..."
            psql "$PSQL_URI" -c "UPDATE alembic_version SET version_num = '$HEAD_REV'"
            return 0
        fi
    done
    echo "Current alembic revision: $CURRENT_REV"
}

echo "=========================================="
echo "MediaFusion Startup"
echo "=========================================="

wait_for_postgres
create_database_if_not_exists
create_extensions
handle_migration_bridge

echo "Running Alembic migrations..."
alembic upgrade head

echo "=========================================="
echo "Starting FastAPI server..."
echo "=========================================="
exec gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --max-requests 500 --max-requests-jitter 200

#!/bin/bash
set -e

# Configuration from environment variables
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-mediafusion}"
DB_USER="${POSTGRES_USER:-mediafusion}"
DB_PASSWORD="${POSTGRES_PASSWORD:-mediafusion}"

# Function to check if PostgreSQL is ready
wait_for_postgres() {
    echo "Waiting for PostgreSQL to be ready..."
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c '\q' 2>/dev/null; then
            echo "PostgreSQL is ready!"
            return 0
        fi
        echo "Attempt $attempt/$max_attempts: PostgreSQL not ready, waiting..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo "ERROR: PostgreSQL did not become ready in time"
    exit 1
}

# Function to create database if it doesn't exist
create_database_if_not_exists() {
    echo "Checking if database '$DB_NAME' exists..."
    
    if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
        echo "Database '$DB_NAME' already exists."
    else
        echo "Creating database '$DB_NAME'..."
        PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME"
        echo "Database '$DB_NAME' created successfully."
    fi
}

# Function to create required extensions
create_extensions() {
    echo "Creating required PostgreSQL extensions..."
    
    # pg_trgm extension for trigram search (full-text search optimization)
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" 2>/dev/null || true
    
    echo "Extensions created successfully."
}

# Main execution
echo "=========================================="
echo "MediaFusion Startup Script"
echo "=========================================="

# Wait for PostgreSQL to be available
wait_for_postgres

# Create database if it doesn't exist
create_database_if_not_exists

# Create required extensions
create_extensions

# Handle upgrade from previous beta versions with consolidated migrations.
# Previous betas had different migration chains that no longer exist.
# If an obsolete revision is detected, we update alembic_version directly
# via SQL (since 'alembic stamp' fails when the current revision is unknown).
handle_migration_bridge() {
    echo "Checking for migration upgrade bridge..."

    # Try to get current alembic version
    CURRENT_REV=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
        "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || echo "")

    # Trim whitespace
    CURRENT_REV=$(echo "$CURRENT_REV" | tr -d '[:space:]')

    if [ -z "$CURRENT_REV" ]; then
        echo "No existing alembic version found (fresh install or new database)."
        return 0
    fi

    # Consolidated base revision (equivalent to beta.1 final schema)
    BASE_REV="050497c15ecc"
    # Current HEAD revision (base + all post-base updates)
    HEAD_REV="f1e2d3c4b5a6"

    # v5.0.0-beta.1 revision hashes (schema = base, stamp to base so upgrade applies post-base updates)
    BETA1_REVISIONS="4829e203ecaf c63392160ce7 bf75239e668e 7f6e3631b327 d1a2b3c4d5e6 8d55e5e54b6a 55db6bd4aab2 64ab9417af09 32962aacd8dc 5633887f53ad a1b2c3d4e5f6 b2c3d4e5f6a7 e7e460f99493"

    # Pre-consolidation development revisions (post-base updates already applied, stamp to HEAD)
    OLD_DEV_REVISIONS="a1b2c3d4e5f6 17334c3ffa57 b6974f19eb9a 641f2ff525e4 302b751510cd a675cdb2e88a c7a8b9d0e1f2 53827f147dc2 d8e9f0a1b2c3 1baf610e47f4 95f08ba65a00"

    # Check if current revision is a known beta.1 revision
    for rev in $BETA1_REVISIONS; do
        if [ "$CURRENT_REV" = "$rev" ]; then
            echo "Detected v5.0.0-beta.1 database (revision: $CURRENT_REV)"
            echo "Updating alembic_version to consolidated base revision ($BASE_REV)..."
            PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c \
                "UPDATE alembic_version SET version_num = '$BASE_REV'"
            echo "Bridge complete. 'alembic upgrade head' will apply remaining updates."
            return 0
        fi
    done

    # Check if current revision is from the old development chain (post-base updates already applied)
    for rev in $OLD_DEV_REVISIONS; do
        if [ "$CURRENT_REV" = "$rev" ]; then
            echo "Detected pre-consolidation development revision ($CURRENT_REV)"
            echo "Updating alembic_version to consolidated HEAD ($HEAD_REV)..."
            PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c \
                "UPDATE alembic_version SET version_num = '$HEAD_REV'"
            echo "Bridge complete. Database is up to date."
            return 0
        fi
    done

    echo "Current alembic revision: $CURRENT_REV"
}

handle_migration_bridge

# Run Alembic migrations
echo "Running Alembic PostgreSQL migrations..."
alembic upgrade head

echo "=========================================="
echo "Starting FastAPI server..."
echo "=========================================="
exec gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --max-requests 500 --max-requests-jitter 200

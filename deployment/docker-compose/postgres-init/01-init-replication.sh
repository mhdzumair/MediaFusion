#!/bin/bash
set -e

# Create replication user for read replicas
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create replication user if it doesn't exist
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator_password';
        END IF;
    END
    \$\$;

    -- Create pg_trgm and btree_gin extensions for full-text search
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS btree_gin;
EOSQL

# Configure pg_hba.conf for replication
echo "host replication replicator all md5" >> "$PGDATA/pg_hba.conf"

echo "PostgreSQL initialization completed."


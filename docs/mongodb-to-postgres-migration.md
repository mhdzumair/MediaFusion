# MongoDB to PostgreSQL Migration Guide

This guide provides detailed instructions for migrating MediaFusion from MongoDB to PostgreSQL. This is a **major change** that affects the core database layer.

## Overview

MediaFusion has transitioned from MongoDB (using Beanie ODM) to PostgreSQL (using SQLModel/SQLAlchemy) for improved:
- **Performance**: Better indexing, full-text search with `pg_trgm`, and optimized queries
- **Data Integrity**: Strong ACID compliance and relational constraints
- **Scalability**: Read replicas support and connection pooling
- **Maintainability**: Standard SQL migrations with Alembic

## Prerequisites

Before starting the migration, ensure you have:

1. **PostgreSQL 15+** installed (with `pg_trgm` extension)
2. **Existing MongoDB instance** with your current data
3. **Sufficient disk space** for both databases during migration
4. **Backup of your MongoDB data**

## Migration Steps

### Step 1: Set Up PostgreSQL

#### Using Docker Compose (Recommended)

```yaml
services:
  postgres:
    image: postgres:18-alpine
    container_name: mediafusion-postgres
    environment:
      POSTGRES_USER: mediafusion
      POSTGRES_PASSWORD: your_secure_password
      POSTGRES_DB: mediafusion
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql
      - ./postgres-init:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mediafusion"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
```

Create `postgres-init/01-init.sh`:
```bash
#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOSQL
```

#### Manual PostgreSQL Setup

```sql
-- Create database
CREATE DATABASE mediafusion;

-- Connect to database
\c mediafusion

-- Enable required extension for text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

### Step 2: Configure Environment Variables

Update your `.env` file or environment configuration:

```env
# PostgreSQL Configuration (Required)
POSTGRES_URI=postgresql+asyncpg://mediafusion:your_password@localhost:5432/mediafusion

# Optional: Read replica for scaling reads
POSTGRES_READ_URI=postgresql+asyncpg://mediafusion:your_password@localhost:5433/mediafusion

# MongoDB (Required during migration only)
MONGO_URI=mongodb://localhost:27017/mediafusion

# Connection pool settings
DB_MAX_CONNECTIONS=50
```

### Step 3: Run Alembic Migrations

Initialize the PostgreSQL schema:

```bash
# Run all migrations to create the schema
alembic upgrade head
```

This creates all necessary tables, indexes, and constraints in PostgreSQL.

### Step 4: Run Data Migration Script

MediaFusion includes a comprehensive migration script that handles:
- Metadata (movies, series, TV channels)
- Torrent streams and TV streams
- Episodes and seasons
- Catalogs and genres
- RSS feeds
- All relationships and references

```bash
# Run the migration (with progress tracking)
python -m migrations.mongo_to_postgres migrate \
    --mongo-uri "mongodb://localhost:27017/mediafusion" \
    --postgres-uri "postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion"
```

#### Migration Options

```bash
# Check migration status
python -m migrations.mongo_to_postgres status \
    --mongo-uri "mongodb://localhost:27017/mediafusion" \
    --postgres-uri "postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion"

# Resume interrupted migration
python -m migrations.mongo_to_postgres migrate --resume \
    --mongo-uri "mongodb://localhost:27017/mediafusion" \
    --postgres-uri "postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion"

# Migrate specific collections only
python -m migrations.mongo_to_postgres migrate --collections movies,series \
    --mongo-uri "mongodb://localhost:27017/mediafusion" \
    --postgres-uri "postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion"
```

### Step 5: Verify Migration

After migration completes:

```bash
# Check record counts match
python -m migrations.mongo_to_postgres verify \
    --mongo-uri "mongodb://localhost:27017/mediafusion" \
    --postgres-uri "postgresql+asyncpg://mediafusion:password@localhost:5432/mediafusion"
```

Manually verify key data:
```sql
-- Check metadata counts
SELECT type, COUNT(*) FROM base_metadata GROUP BY type;

-- Check torrent streams
SELECT COUNT(*) FROM torrent_stream;

-- Check TV streams
SELECT COUNT(*) FROM tv_stream;
```

### Step 6: Update Application Configuration

Once migration is verified, update your deployment:

1. **Remove MongoDB dependency** from your docker-compose or k8s configuration
2. **Remove `MONGO_URI`** from environment variables (no longer needed)
3. **Restart the application** - it will now use PostgreSQL exclusively

## Post-Migration

### Clear Redis Cache

After migration, clear the Redis cache to ensure fresh data:

```bash
redis-cli FLUSHALL
```

Or selectively:
```bash
redis-cli KEYS "catalog:*" | xargs redis-cli DEL
redis-cli KEYS "meta:*" | xargs redis-cli DEL
redis-cli KEYS "streams:*" | xargs redis-cli DEL
```

### Database Optimization

Run these PostgreSQL optimizations after migration:

```sql
-- Analyze all tables for query optimization
ANALYZE;

-- Vacuum to reclaim space
VACUUM ANALYZE;

-- Rebuild indexes if needed
REINDEX DATABASE mediafusion;
```

## Rollback Procedure

If you need to rollback to MongoDB:

1. **Keep MongoDB running** during the transition period
2. **Restore from backup** if data was modified
3. **Update environment** to remove PostgreSQL settings
4. **Revert startup script** to use Beanie migrations

## Schema Changes

### Key Differences from MongoDB

| MongoDB | PostgreSQL |
|---------|------------|
| Document-based | Relational tables |
| Embedded episodes | Separate `series_episode` table |
| Dynamic schema | Strict schema with migrations |
| ObjectId | String IDs (IMDB IDs, info_hash) |

### Table Structure

```
base_metadata          - Common metadata (id, title, year, poster, etc.)
├── movie_metadata     - Movie-specific fields (runtime, etc.)
├── series_metadata    - Series-specific fields
└── tv_metadata        - TV channel fields

torrent_stream         - Torrent streams (id = info_hash)
├── torrent_stream_file     - Individual files in torrents
└── series_episode          - Episode mappings for series

tv_stream              - Live TV streams

catalog                - Catalog definitions
genre                  - Genre definitions
rss_feed               - RSS feed configurations
```

## Troubleshooting

### Common Issues

#### 1. "pg_trgm extension not found"
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

#### 2. Connection pool exhausted
Increase `DB_MAX_CONNECTIONS` in your environment.

#### 3. Migration timeout
Use the `--batch-size` option to reduce batch sizes:
```bash
python -m migrations.mongo_to_postgres migrate --batch-size 500
```

#### 4. Duplicate key errors
The migration script handles duplicates by using `ON CONFLICT DO UPDATE`. If you see these errors, they are usually informational.

### Getting Help

- Check logs in `migrations.log`
- Review the migration status with the `status` command
- Open an issue on GitHub with migration logs

## Performance Tuning

### PostgreSQL Configuration

For large datasets, optimize `postgresql.conf`:

```ini
# Memory settings
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 64MB

# Connection settings
max_connections = 100

# Write performance
wal_buffers = 16MB
checkpoint_completion_target = 0.9
```

### Read Replicas

For high-traffic instances, configure read replicas:

```env
POSTGRES_URI=postgresql+asyncpg://user:pass@primary:5432/mediafusion
POSTGRES_READ_URI=postgresql+asyncpg://user:pass@replica:5432/mediafusion
```

## Data Size Estimates

| Records | MongoDB Size | PostgreSQL Size | Migration Time |
|---------|--------------|-----------------|----------------|
| 100K | ~500MB | ~400MB | ~5 min |
| 1M | ~5GB | ~4GB | ~30 min |
| 10M | ~50GB | ~40GB | ~4 hours |

*Times may vary based on hardware and network configuration.*


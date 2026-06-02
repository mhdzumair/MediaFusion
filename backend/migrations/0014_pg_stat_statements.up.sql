-- Enable pg_stat_statements for admin slow-query diagnostics.
--
-- Requires PostgreSQL started with:
--   shared_preload_libraries = 'pg_stat_statements'
-- If preload was just added, restart Postgres once, then this migration (or
-- CREATE EXTENSION) will succeed. Without preload, creation is skipped so
-- application startup is not blocked on misconfigured instances.

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE
            'pg_stat_statements not enabled (%). Add shared_preload_libraries=pg_stat_statements, restart PostgreSQL, then run: CREATE EXTENSION IF NOT EXISTS pg_stat_statements;',
            SQLERRM;
END $$;

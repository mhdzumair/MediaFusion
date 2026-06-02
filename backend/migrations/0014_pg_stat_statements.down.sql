-- Optional rollback; removes slow-query stats views (safe on dev).
DROP EXTENSION IF EXISTS pg_stat_statements;

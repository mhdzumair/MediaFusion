"""
Database Admin API endpoints for database management operations.
Provides stats, table browsing, schema viewing, maintenance, import/export, and query execution.
Admin-only access.
"""

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db.database import ASYNC_ENGINE, get_async_session
from db.enums import UserRole
from db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/db", tags=["Admin Database Management"])


# ============================================
# Pydantic Schemas
# ============================================


class DatabaseStats(BaseModel):
    """PostgreSQL database statistics."""

    version: str
    database_name: str
    size_human: str
    total_size_bytes: int
    connection_count: int
    max_connections: int
    cache_hit_ratio: float
    uptime_seconds: int
    active_queries: int
    deadlocks: int
    transactions_committed: int
    transactions_rolled_back: int


class TableInfo(BaseModel):
    """Information about a database table."""

    name: str
    schema_name: str
    row_count: int
    size_human: str
    size_bytes: int
    index_size_human: str
    index_size_bytes: int
    last_vacuum: str | None = None
    last_analyze: str | None = None
    last_autovacuum: str | None = None
    last_autoanalyze: str | None = None


class TablesListResponse(BaseModel):
    """Response for listing all tables."""

    tables: list[TableInfo]
    total_count: int
    total_size_human: str
    total_size_bytes: int


class ColumnInfo(BaseModel):
    """Information about a table column."""

    name: str
    data_type: str
    is_nullable: bool
    default_value: str | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    foreign_key_ref: str | None = None


class IndexInfo(BaseModel):
    """Information about a table index."""

    name: str
    columns: list[str]
    is_unique: bool
    is_primary: bool
    index_type: str


class ForeignKeyInfo(BaseModel):
    """Information about a foreign key constraint."""

    name: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]


class TableSchema(BaseModel):
    """Complete schema information for a table."""

    name: str
    schema_name: str
    columns: list[ColumnInfo]
    indexes: list[IndexInfo]
    foreign_keys: list[ForeignKeyInfo]
    row_count: int
    size_human: str


class TableDataResponse(BaseModel):
    """Response for table data with pagination."""

    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    page: int
    per_page: int
    pages: int


class FilterCondition(BaseModel):
    """A single filter condition for table data queries."""

    column: str
    operator: str = Field(
        default="equals",
        description="Filter operator: equals, not_equals, contains, starts_with, ends_with, is_null, is_not_null, gt, gte, lt, lte, array_contains, array_not_contains, array_empty, array_not_empty, array_length_eq, array_length_gt, json_is_null, json_is_not_null",
    )
    value: str | None = None


class OrphanRecord(BaseModel):
    """Information about an orphaned record."""

    table: str
    id: str
    reason: str
    created_at: str | None = None


class OrphansResponse(BaseModel):
    """Response for orphan detection."""

    orphans: list[OrphanRecord]
    total_count: int
    by_type: dict[str, int]



class MaintenanceRequest(BaseModel):
    """Request for maintenance operations."""

    tables: list[str] | None = None  # None means all tables
    operation: Literal["vacuum", "analyze", "vacuum_analyze", "reindex"]
    full: bool = False  # For VACUUM FULL


class MaintenanceResult(BaseModel):
    """Result of a maintenance operation."""

    success: bool
    operation: str
    tables_processed: list[str]
    execution_time_ms: float
    message: str


class BulkDeleteRequest(BaseModel):
    """Request for bulk delete operation."""

    table: str
    ids: list[str]
    id_column: str = "id"
    cascade: bool = False  # If True, delete related records in child tables first


class BulkUpdateRequest(BaseModel):
    """Request for bulk update operation."""

    table: str
    ids: list[str]
    id_column: str = "id"
    updates: dict[str, Any]


class BulkOperationResult(BaseModel):
    """Result of a bulk operation."""

    success: bool
    rows_affected: int
    execution_time_ms: float
    errors: list[str] = []


class ExportRequest(BaseModel):
    """Request for data export."""

    format: Literal["csv", "json", "sql"]
    include_schema: bool = True  # For SQL: include CREATE TABLE
    include_data: bool = True  # For SQL: include INSERT statements
    where_clause: str | None = None
    limit: int | None = None


class ImportPreviewResponse(BaseModel):
    """Response for import preview."""

    total_rows: int
    sample_rows: list[dict[str, Any]]
    detected_columns: list[str]
    table_columns: list[str]
    column_mapping: dict[str, str]
    validation_errors: list[str]
    warnings: list[str]


class ImportRequest(BaseModel):
    """Request for data import."""

    table: str
    mode: Literal["insert", "upsert", "replace"]
    column_mapping: dict[str, str] | None = None
    skip_errors: bool = False


class ImportResult(BaseModel):
    """Result of a data import."""

    success: bool
    rows_imported: int
    rows_updated: int
    rows_skipped: int
    errors: list[str]
    execution_time_ms: float


# ============================================
# Helper Functions
# ============================================


def format_bytes(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"


def serialize_row(row: Any) -> dict[str, Any]:
    """Serialize a database row to a JSON-compatible dictionary."""
    if hasattr(row, "_mapping"):
        row = dict(row._mapping)
    elif hasattr(row, "__dict__"):
        row = {k: v for k, v in row.__dict__.items() if not k.startswith("_")}

    result = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, bytes):
            result[key] = f"<binary:{len(value)} bytes>"
        elif hasattr(value, "__dict__"):
            result[key] = str(value)
        else:
            result[key] = value
    return result


# ============================================
# Database Stats Endpoints
# ============================================


@router.get("/stats", response_model=DatabaseStats)
async def get_database_stats(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get PostgreSQL database statistics."""
    try:
        # Get database version
        version_result = await session.execute(text("SELECT version()"))
        version = version_result.scalar()

        # Get database name
        db_name_result = await session.execute(text("SELECT current_database()"))
        db_name = db_name_result.scalar()

        # Get database size
        size_result = await session.execute(text("SELECT pg_database_size(current_database())"))
        total_size = size_result.scalar() or 0

        # Get connection stats
        conn_result = await session.execute(
            text("""
            SELECT
                count(*) as current_connections,
                (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') as max_connections
            FROM pg_stat_activity
            WHERE datname = current_database()
        """)
        )
        conn_row = conn_result.first()
        current_connections = conn_row[0] if conn_row else 0
        max_connections = conn_row[1] if conn_row else 100

        # Get cache hit ratio
        cache_result = await session.execute(
            text("""
            SELECT
                CASE
                    WHEN (sum(heap_blks_hit) + sum(heap_blks_read)) = 0 THEN 0
                    ELSE round(sum(heap_blks_hit) * 100.0 / (sum(heap_blks_hit) + sum(heap_blks_read)), 2)
                END as cache_hit_ratio
            FROM pg_statio_user_tables
        """)
        )
        cache_hit_ratio = cache_result.scalar() or 0

        # Get uptime
        uptime_result = await session.execute(
            text("""
            SELECT EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))::int
            FROM pg_postmaster_start_time()
        """)
        )
        uptime = uptime_result.scalar() or 0

        # Get active queries count
        active_result = await session.execute(
            text("""
            SELECT count(*) FROM pg_stat_activity
            WHERE state = 'active' AND pid != pg_backend_pid()
        """)
        )
        active_queries = active_result.scalar() or 0

        # Get transaction stats
        stats_result = await session.execute(
            text("""
            SELECT
                COALESCE(xact_commit, 0) as commits,
                COALESCE(xact_rollback, 0) as rollbacks,
                COALESCE(deadlocks, 0) as deadlocks
            FROM pg_stat_database
            WHERE datname = current_database()
        """)
        )
        stats_row = stats_result.first()

        return DatabaseStats(
            version=version[:100] if version else "Unknown",
            database_name=db_name or "Unknown",
            size_human=format_bytes(total_size),
            total_size_bytes=total_size,
            connection_count=current_connections,
            max_connections=max_connections,
            cache_hit_ratio=float(cache_hit_ratio),
            uptime_seconds=uptime,
            active_queries=active_queries,
            deadlocks=stats_row[2] if stats_row else 0,
            transactions_committed=stats_row[0] if stats_row else 0,
            transactions_rolled_back=stats_row[1] if stats_row else 0,
        )
    except Exception as e:
        logger.exception(f"Error getting database stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get database stats: {str(e)}",
        )


# ============================================
# Table Listing and Schema Endpoints
# ============================================


@router.get("/tables", response_model=TablesListResponse)
async def list_tables(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all tables with their sizes and statistics."""
    try:
        # Use pg_class.reltuples for row count estimates as it is more reliably
        # updated than pg_stat_user_tables.n_live_tup (which stays 0 until ANALYZE runs).
        result = await session.execute(
            text("""
            SELECT
                s.schemaname,
                s.relname as table_name,
                GREATEST(c.reltuples::bigint, s.n_live_tup) as row_count,
                pg_total_relation_size(quote_ident(s.schemaname) || '.' || quote_ident(s.relname)) as total_size,
                pg_indexes_size(quote_ident(s.schemaname) || '.' || quote_ident(s.relname)) as index_size,
                s.last_vacuum,
                s.last_analyze,
                s.last_autovacuum,
                s.last_autoanalyze
            FROM pg_stat_user_tables s
            JOIN pg_class c ON c.relname = s.relname
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
            ORDER BY pg_total_relation_size(quote_ident(s.schemaname) || '.' || quote_ident(s.relname)) DESC
        """)
        )

        tables = []
        total_size = 0

        for row in result.all():
            table_size = row[3] or 0
            index_size = row[4] or 0
            total_size += table_size

            tables.append(
                TableInfo(
                    name=row[1],
                    schema_name=row[0],
                    row_count=max(row[2] or 0, 0),
                    size_human=format_bytes(table_size),
                    size_bytes=table_size,
                    index_size_human=format_bytes(index_size),
                    index_size_bytes=index_size,
                    last_vacuum=row[5].isoformat() if row[5] else None,
                    last_analyze=row[6].isoformat() if row[6] else None,
                    last_autovacuum=row[7].isoformat() if row[7] else None,
                    last_autoanalyze=row[8].isoformat() if row[8] else None,
                )
            )

        return TablesListResponse(
            tables=tables,
            total_count=len(tables),
            total_size_human=format_bytes(total_size),
            total_size_bytes=total_size,
        )
    except Exception as e:
        logger.exception(f"Error listing tables: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list tables: {str(e)}",
        )


@router.get("/tables/{table_name}/schema", response_model=TableSchema)
async def get_table_schema(
    table_name: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get detailed schema information for a specific table."""
    try:
        # Verify table exists
        exists_result = await session.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table_name
                )
            """),
            {"table_name": table_name},
        )

        if not exists_result.scalar():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_name}' not found",
            )

        # Get columns
        cols_result = await session.execute(
            text("""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    c.column_default,
                    CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END as is_pk,
                    CASE WHEN fk.column_name IS NOT NULL THEN true ELSE false END as is_fk,
                    fk.foreign_table_name || '.' || fk.foreign_column_name as fk_ref
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    WHERE tc.table_name = :table_name AND tc.constraint_type = 'PRIMARY KEY'
                ) pk ON c.column_name = pk.column_name
                LEFT JOIN (
                    SELECT
                        kcu.column_name,
                        ccu.table_name as foreign_table_name,
                        ccu.column_name as foreign_column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.table_name = :table_name AND tc.constraint_type = 'FOREIGN KEY'
                ) fk ON c.column_name = fk.column_name
                WHERE c.table_name = :table_name AND c.table_schema = 'public'
                ORDER BY c.ordinal_position
            """),
            {"table_name": table_name},
        )

        columns = [
            ColumnInfo(
                name=row[0],
                data_type=row[1],
                is_nullable=row[2] == "YES",
                default_value=row[3],
                is_primary_key=row[4],
                is_foreign_key=row[5],
                foreign_key_ref=row[6],
            )
            for row in cols_result.all()
        ]

        # Get indexes
        idx_result = await session.execute(
            text("""
                SELECT
                    i.relname as index_name,
                    array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) as columns,
                    ix.indisunique as is_unique,
                    ix.indisprimary as is_primary,
                    am.amname as index_type
                FROM pg_index ix
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN pg_class t ON t.oid = ix.indrelid
                JOIN pg_am am ON am.oid = i.relam
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
                WHERE t.relname = :table_name
                GROUP BY i.relname, ix.indisunique, ix.indisprimary, am.amname
            """),
            {"table_name": table_name},
        )

        indexes = [
            IndexInfo(
                name=row[0],
                columns=row[1] or [],
                is_unique=row[2],
                is_primary=row[3],
                index_type=row[4],
            )
            for row in idx_result.all()
        ]

        # Get foreign keys
        fk_result = await session.execute(
            text("""
                SELECT
                    tc.constraint_name,
                    array_agg(DISTINCT kcu.column_name) as columns,
                    ccu.table_name as foreign_table,
                    array_agg(DISTINCT ccu.column_name) as foreign_columns
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.table_name = :table_name
                    AND tc.constraint_type = 'FOREIGN KEY'
                GROUP BY tc.constraint_name, ccu.table_name
            """),
            {"table_name": table_name},
        )

        foreign_keys = [
            ForeignKeyInfo(
                name=row[0],
                columns=row[1] or [],
                referenced_table=row[2],
                referenced_columns=row[3] or [],
            )
            for row in fk_result.all()
        ]

        # Get table stats - use pg_class.reltuples for more accurate row counts
        stats_result = await session.execute(
            text("""
                SELECT
                    GREATEST(c.reltuples::bigint, s.n_live_tup) as row_count,
                    pg_total_relation_size(quote_ident('public') || '.' || quote_ident(:table_name))
                FROM pg_stat_user_tables s
                JOIN pg_class c ON c.relname = s.relname
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
                WHERE s.relname = :table_name
            """),
            {"table_name": table_name},
        )
        stats_row = stats_result.first()

        return TableSchema(
            name=table_name,
            schema_name="public",
            columns=columns,
            indexes=indexes,
            foreign_keys=foreign_keys,
            row_count=max(stats_row[0] or 0, 0) if stats_row else 0,
            size_human=format_bytes(stats_row[1]) if stats_row else "0 B",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting table schema: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table schema: {str(e)}",
        )


@router.get("/tables/{table_name}/data", response_model=TableDataResponse)
async def get_table_data(
    table_name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    order_by: str | None = None,
    order_dir: Literal["asc", "desc"] = "asc",
    search: str | None = Query(None, description="Search term to filter text columns"),
    filters: str | None = Query(
        None,
        description="JSON array of filter conditions: [{column, operator, value}]. Operators: equals, not_equals, contains, starts_with, ends_with, is_null, is_not_null, gt, gte, lt, lte, array_contains, array_not_contains, array_empty, array_not_empty, array_length_eq, array_length_gt, json_is_null, json_is_not_null",
    ),
    # Legacy single filter support (deprecated, use filters instead)
    filter_column: str | None = Query(None, description="[Deprecated] Column name to filter on - use filters param"),
    filter_operator: str | None = Query(
        "equals",
        description="[Deprecated] Filter operator - use filters param",
    ),
    filter_value: str | None = Query(None, description="[Deprecated] Value to filter by - use filters param"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get paginated data from a table with optional search/filter. Supports multiple filters via JSON array."""
    try:
        # Verify table exists and get columns with types
        cols_result = await session.execute(
            text("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                ORDER BY ordinal_position
            """),
            {"table_name": table_name},
        )

        column_info = [(row[0], row[1]) for row in cols_result.all()]
        columns = [c[0] for c in column_info]
        col_types = {c[0]: c[1] for c in column_info}

        if not columns:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_name}' not found",
            )

        # Validate order_by column
        if order_by and order_by not in columns:
            order_by = None

        # Build WHERE clause for search/filter
        where_clauses = []
        params = {}

        # Search across text-like columns
        if search:
            text_columns = [
                c[0] for c in column_info if any(t in c[1].lower() for t in ["text", "char", "varchar", "uuid"])
            ]
            if text_columns:
                search_conditions = " OR ".join([f"CAST({col} AS TEXT) ILIKE :search" for col in text_columns])
                where_clauses.append(f"({search_conditions})")
                params["search"] = f"%{search}%"

        # Helper function to build a single filter condition
        def build_filter_clause(col: str, op: str, val: str | None, param_idx: int) -> tuple[str | None, dict]:
            """Build a single filter clause and return (clause, params_dict)."""
            col_type = col_types.get(col, "").lower()
            is_array = "array" in col_type or col_type.startswith("_")
            is_json = "json" in col_type
            param_name = f"filter_value_{param_idx}"
            local_params = {}

            if op == "is_null":
                return f"{col} IS NULL", local_params
            elif op == "is_not_null":
                return f"{col} IS NOT NULL", local_params
            elif op == "json_is_null":
                # For JSON/JSONB columns, check for SQL NULL OR JSON null value
                return f"({col} IS NULL OR {col}::text = 'null')", local_params
            elif op == "json_is_not_null":
                # For JSON/JSONB columns, check for NOT SQL NULL AND NOT JSON null value
                return f"({col} IS NOT NULL AND {col}::text != 'null')", local_params
            elif op == "equals" and val is not None:
                local_params[param_name] = val
                return f"CAST({col} AS TEXT) = :{param_name}", local_params
            elif op == "not_equals" and val is not None:
                local_params[param_name] = val
                return f"CAST({col} AS TEXT) != :{param_name}", local_params
            elif op == "contains" and val is not None:
                local_params[param_name] = f"%{val}%"
                return f"CAST({col} AS TEXT) ILIKE :{param_name}", local_params
            elif op == "starts_with" and val is not None:
                local_params[param_name] = f"{val}%"
                return f"CAST({col} AS TEXT) ILIKE :{param_name}", local_params
            elif op == "ends_with" and val is not None:
                local_params[param_name] = f"%{val}"
                return f"CAST({col} AS TEXT) ILIKE :{param_name}", local_params
            elif op == "gt" and val is not None:
                local_params[param_name] = val
                return f"{col} > :{param_name}", local_params
            elif op == "gte" and val is not None:
                local_params[param_name] = val
                return f"{col} >= :{param_name}", local_params
            elif op == "lt" and val is not None:
                local_params[param_name] = val
                return f"{col} < :{param_name}", local_params
            elif op == "lte" and val is not None:
                local_params[param_name] = val
                return f"{col} <= :{param_name}", local_params
            # Array-specific operators
            elif op == "array_contains" and val is not None and is_array:
                local_params[param_name] = val
                return f":{param_name} = ANY({col})", local_params
            elif op == "array_not_contains" and val is not None and is_array:
                local_params[param_name] = val
                return f"NOT (:{param_name} = ANY({col}))", local_params
            elif op == "array_empty" and is_array:
                return f"(COALESCE(array_length({col}, 1), 0) = 0)", local_params
            elif op == "array_not_empty" and is_array:
                return f"(array_length({col}, 1) > 0)", local_params
            elif op == "array_length_eq" and val is not None and is_array:
                local_params[param_name] = int(val)
                return f"COALESCE(array_length({col}, 1), 0) = :{param_name}", local_params
            elif op == "array_length_gt" and val is not None and is_array:
                local_params[param_name] = int(val)
                return f"COALESCE(array_length({col}, 1), 0) > :{param_name}", local_params
            return None, local_params

        # Parse and apply multiple filters from JSON
        filter_conditions: list[FilterCondition] = []

        # Parse new filters JSON param
        if filters:
            try:
                filters_data = json.loads(filters)
                if isinstance(filters_data, list):
                    for f in filters_data:
                        if isinstance(f, dict) and "column" in f:
                            filter_conditions.append(
                                FilterCondition(
                                    column=f["column"],
                                    operator=f.get("operator", "equals"),
                                    value=f.get("value"),
                                )
                            )
            except json.JSONDecodeError:
                pass  # Ignore invalid JSON, fall through to legacy params

        # Legacy single filter support (backwards compatibility)
        if not filter_conditions and filter_column and filter_column in columns:
            filter_conditions.append(
                FilterCondition(
                    column=filter_column,
                    operator=filter_operator or "equals",
                    value=filter_value,
                )
            )

        # Apply all filter conditions
        for idx, fc in enumerate(filter_conditions):
            if fc.column not in columns:
                continue
            clause, clause_params = build_filter_clause(fc.column, fc.operator, fc.value, idx)
            if clause:
                where_clauses.append(clause)
                params.update(clause_params)

        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)

        # Get total count with filters
        count_query = f"SELECT COUNT(*) FROM {table_name}{where_sql}"
        count_result = await session.execute(text(count_query), params)
        total = count_result.scalar() or 0

        # Build query
        offset = (page - 1) * per_page
        order_clause = ""
        if order_by:
            order_clause = f" ORDER BY {order_by} {order_dir.upper()}"

        query = f"SELECT * FROM {table_name}{where_sql}{order_clause} LIMIT :limit OFFSET :offset"
        params["limit"] = per_page
        params["offset"] = offset
        result = await session.execute(text(query), params)

        rows = [serialize_row(row) for row in result.all()]

        return TableDataResponse(
            table=table_name,
            columns=columns,
            rows=rows,
            total=total,
            page=page,
            per_page=per_page,
            pages=(total + per_page - 1) // per_page if total > 0 else 1,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting table data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table data: {str(e)}",
        )


# ============================================
# Orphan Detection Endpoints
# ============================================


@router.get("/orphans", response_model=OrphansResponse)
async def find_orphans(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Find orphaned records in the database.

    Uses the correct v5+ schema:
    - stream: base stream table
    - torrent_stream: torrent-specific data (links via stream_id to stream)
    - stream_media_link: links streams to media (stream_id -> media_id)
    - stream_file: files within streams (links via stream_id to stream)
    """
    try:
        orphans = []
        by_type: dict[str, int] = {}

        # Find streams without any media links
        # This checks for streams that have no entry in stream_media_link
        result = await session.execute(
            text("""
            SELECT s.id, s.created_at
            FROM stream s
            LEFT JOIN stream_media_link sml ON s.id = sml.stream_id
            WHERE sml.id IS NULL
            LIMIT 1000
        """)
        )
        for row in result.all():
            orphans.append(
                OrphanRecord(
                    table="stream",
                    id=str(row[0]),
                    reason="No media linkage (missing stream_media_link entry)",
                    created_at=row[1].isoformat() if row[1] else None,
                )
            )
        by_type["stream"] = len([o for o in orphans if o.table == "stream"])

        # Find torrent_stream entries without parent stream
        result = await session.execute(
            text("""
            SELECT ts.id, ts.created_at
            FROM torrent_stream ts
            LEFT JOIN stream s ON ts.stream_id = s.id
            WHERE s.id IS NULL
            LIMIT 1000
        """)
        )
        for row in result.all():
            orphans.append(
                OrphanRecord(
                    table="torrent_stream",
                    id=str(row[0]),
                    reason="Missing parent stream record",
                    created_at=row[1].isoformat() if row[1] else None,
                )
            )
        by_type["torrent_stream"] = len([o for o in orphans if o.table == "torrent_stream"])

        # Find stream_file entries without parent stream
        result = await session.execute(
            text("""
            SELECT sf.id, NULL as created_at
            FROM stream_file sf
            LEFT JOIN stream s ON sf.stream_id = s.id
            WHERE s.id IS NULL
            LIMIT 1000
        """)
        )
        for row in result.all():
            orphans.append(
                OrphanRecord(
                    table="stream_file",
                    id=str(row[0]),
                    reason="Missing parent stream record",
                    created_at=None,
                )
            )
        by_type["stream_file"] = len([o for o in orphans if o.table == "stream_file"])

        # Find media without any streams (movies/series only)
        # Use type::text to convert enum to string for comparison
        result = await session.execute(
            text("""
            SELECT m.id, m.created_at
            FROM media m
            LEFT JOIN stream_media_link sml ON m.id = sml.media_id
            WHERE sml.id IS NULL
                AND m.type::text IN ('movie', 'series')
            LIMIT 1000
        """)
        )
        for row in result.all():
            orphans.append(
                OrphanRecord(
                    table="media",
                    id=str(row[0]),
                    reason="No associated streams",
                    created_at=row[1].isoformat() if row[1] else None,
                )
            )
        by_type["media"] = len([o for o in orphans if o.table == "media"])

        # Find stream_media_link entries pointing to non-existent media
        result = await session.execute(
            text("""
            SELECT sml.id, NULL as created_at
            FROM stream_media_link sml
            LEFT JOIN media m ON sml.media_id = m.id
            WHERE m.id IS NULL
            LIMIT 1000
        """)
        )
        for row in result.all():
            orphans.append(
                OrphanRecord(
                    table="stream_media_link",
                    id=str(row[0]),
                    reason="Points to non-existent media",
                    created_at=None,
                )
            )
        by_type["stream_media_link"] = len([o for o in orphans if o.table == "stream_media_link"])

        return OrphansResponse(
            orphans=orphans,
            total_count=len(orphans),
            by_type=by_type,
        )
    except Exception as e:
        logger.exception(f"Error finding orphans: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to find orphans: {str(e)}",
        )


@router.post("/orphans/cleanup")
async def cleanup_orphans(
    tables: list[str] | None = None,
    dry_run: bool = Query(True, description="If true, only report what would be deleted"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Clean up orphaned records from the database.

    Uses the correct v5+ schema for orphan detection and cleanup.
    """
    try:
        results = {
            "dry_run": dry_run,
            "deleted": {},
            "would_delete": {},
        }

        target_tables = tables or [
            "stream_file",
            "torrent_stream",
            "stream",
            "stream_media_link",
        ]

        for table in target_tables:
            count = 0

            if table == "stream_file":
                # Find stream_file entries without parent stream
                if dry_run:
                    result = await session.execute(
                        text("""
                        SELECT COUNT(*) FROM stream_file sf
                        LEFT JOIN stream s ON sf.stream_id = s.id
                        WHERE s.id IS NULL
                    """)
                    )
                    count = result.scalar() or 0
                else:
                    result = await session.execute(
                        text("""
                        DELETE FROM stream_file sf
                        USING (
                            SELECT sf2.id FROM stream_file sf2
                            LEFT JOIN stream s ON sf2.stream_id = s.id
                            WHERE s.id IS NULL
                        ) orphans
                        WHERE sf.id = orphans.id
                    """)
                    )
                    count = result.rowcount

            elif table == "torrent_stream":
                # Find torrent_stream entries without parent stream
                if dry_run:
                    result = await session.execute(
                        text("""
                        SELECT COUNT(*) FROM torrent_stream ts
                        LEFT JOIN stream s ON ts.stream_id = s.id
                        WHERE s.id IS NULL
                    """)
                    )
                    count = result.scalar() or 0
                else:
                    result = await session.execute(
                        text("""
                        DELETE FROM torrent_stream ts
                        USING (
                            SELECT ts2.id FROM torrent_stream ts2
                            LEFT JOIN stream s ON ts2.stream_id = s.id
                            WHERE s.id IS NULL
                        ) orphans
                        WHERE ts.id = orphans.id
                    """)
                    )
                    count = result.rowcount

            elif table == "stream":
                # Find streams without any media linkage
                if dry_run:
                    result = await session.execute(
                        text("""
                        SELECT COUNT(*) FROM stream s
                        LEFT JOIN stream_media_link sml ON s.id = sml.stream_id
                        WHERE sml.id IS NULL
                    """)
                    )
                    count = result.scalar() or 0
                else:
                    result = await session.execute(
                        text("""
                        DELETE FROM stream s
                        USING (
                            SELECT s2.id FROM stream s2
                            LEFT JOIN stream_media_link sml ON s2.id = sml.stream_id
                            WHERE sml.id IS NULL
                        ) orphans
                        WHERE s.id = orphans.id
                    """)
                    )
                    count = result.rowcount

            elif table == "stream_media_link":
                # Find stream_media_link entries pointing to non-existent media
                if dry_run:
                    result = await session.execute(
                        text("""
                        SELECT COUNT(*) FROM stream_media_link sml
                        LEFT JOIN media m ON sml.media_id = m.id
                        WHERE m.id IS NULL
                    """)
                    )
                    count = result.scalar() or 0
                else:
                    result = await session.execute(
                        text("""
                        DELETE FROM stream_media_link sml
                        USING (
                            SELECT sml2.id FROM stream_media_link sml2
                            LEFT JOIN media m ON sml2.media_id = m.id
                            WHERE m.id IS NULL
                        ) orphans
                        WHERE sml.id = orphans.id
                    """)
                    )
                    count = result.rowcount

            if dry_run:
                results["would_delete"][table] = count
            else:
                results["deleted"][table] = count

        if not dry_run:
            await session.commit()
            logger.info(f"Admin cleaned up orphans: {results['deleted']}")

        return results
    except Exception as e:
        logger.exception(f"Error cleaning up orphans: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cleanup orphans: {str(e)}",
        )


# ============================================
# Maintenance Endpoints
# ============================================


@router.post("/maintenance/vacuum", response_model=MaintenanceResult)
async def vacuum_tables(
    request: MaintenanceRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Run VACUUM on specified tables."""
    import time

    try:
        start_time = time.time()

        # Get list of tables
        if request.tables:
            tables = request.tables
        else:
            result = await session.execute(text("SELECT relname FROM pg_stat_user_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in result.all()]

        processed = []

        # VACUUM requires autocommit mode, so we use raw connection
        async with ASYNC_ENGINE.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")

            for table in tables:
                try:
                    vacuum_cmd = f"VACUUM {'FULL ' if request.full else ''}{table}"
                    await conn.execute(text(vacuum_cmd))
                    processed.append(table)
                except Exception as e:
                    logger.warning(f"Failed to vacuum {table}: {e}")

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin ran VACUUM on {len(processed)} tables")

        return MaintenanceResult(
            success=True,
            operation="vacuum" + (" full" if request.full else ""),
            tables_processed=processed,
            execution_time_ms=execution_time_ms,
            message=f"VACUUM completed on {len(processed)} tables",
        )
    except Exception as e:
        logger.exception(f"Error running VACUUM: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"VACUUM failed: {str(e)}",
        )


@router.post("/maintenance/analyze", response_model=MaintenanceResult)
async def analyze_tables(
    request: MaintenanceRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Run ANALYZE on specified tables."""
    import time

    try:
        start_time = time.time()

        # Get list of tables
        if request.tables:
            tables = request.tables
        else:
            result = await session.execute(text("SELECT relname FROM pg_stat_user_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in result.all()]

        processed = []

        for table in tables:
            try:
                await session.execute(text(f"ANALYZE {table}"))
                processed.append(table)
            except Exception as e:
                logger.warning(f"Failed to analyze {table}: {e}")

        await session.commit()

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin ran ANALYZE on {len(processed)} tables")

        return MaintenanceResult(
            success=True,
            operation="analyze",
            tables_processed=processed,
            execution_time_ms=execution_time_ms,
            message=f"ANALYZE completed on {len(processed)} tables",
        )
    except Exception as e:
        logger.exception(f"Error running ANALYZE: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ANALYZE failed: {str(e)}",
        )


@router.post("/maintenance/reindex", response_model=MaintenanceResult)
async def reindex_tables(
    request: MaintenanceRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Run REINDEX on specified tables."""
    import time

    try:
        start_time = time.time()

        # Get list of tables
        if request.tables:
            tables = request.tables
        else:
            result = await session.execute(text("SELECT relname FROM pg_stat_user_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in result.all()]

        processed = []

        # REINDEX requires special handling
        async with ASYNC_ENGINE.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")

            for table in tables:
                try:
                    await conn.execute(text(f"REINDEX TABLE {table}"))
                    processed.append(table)
                except Exception as e:
                    logger.warning(f"Failed to reindex {table}: {e}")

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin ran REINDEX on {len(processed)} tables")

        return MaintenanceResult(
            success=True,
            operation="reindex",
            tables_processed=processed,
            execution_time_ms=execution_time_ms,
            message=f"REINDEX completed on {len(processed)} tables",
        )
    except Exception as e:
        logger.exception(f"Error running REINDEX: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"REINDEX failed: {str(e)}",
        )


# ============================================
# Bulk Operations Endpoints
# ============================================


@router.post("/bulk/delete", response_model=BulkOperationResult)
async def bulk_delete(
    request: BulkDeleteRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk delete records from a table."""
    import time

    from sqlalchemy.exc import IntegrityError

    try:
        start_time = time.time()

        # Get the ID column type to properly convert values
        col_type_result = await session.execute(
            text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
            """),
            {"table_name": request.table, "column_name": request.id_column},
        )

        col_type_row = col_type_result.first()
        if not col_type_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{request.table}' or column '{request.id_column}' not found",
            )

        id_col_type = col_type_row[0].lower()

        # Build params with proper type conversion
        placeholders = ", ".join([f":id_{i}" for i in range(len(request.ids))])
        params = {}
        for i, id_val in enumerate(request.ids):
            if "int" in id_col_type:
                params[f"id_{i}"] = int(id_val)
            else:
                params[f"id_{i}"] = id_val

        total_deleted = 0

        # If cascade is enabled, find and delete child records first
        if request.cascade:
            # Find all foreign keys referencing this table
            fk_result = await session.execute(
                text("""
                    SELECT
                        tc.table_name AS child_table,
                        kcu.column_name AS child_column,
                        ccu.column_name AS parent_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND ccu.table_name = :table_name
                        AND tc.table_schema = 'public'
                """),
                {"table_name": request.table},
            )

            foreign_keys = fk_result.all()

            # Delete from child tables first
            for child_table, child_column, parent_column in foreign_keys:
                # Recursively delete from child tables
                delete_child_query = f"""
                    DELETE FROM {child_table}
                    WHERE {child_column} IN (
                        SELECT {parent_column} FROM {request.table}
                        WHERE {request.id_column} IN ({placeholders})
                    )
                """
                child_result = await session.execute(text(delete_child_query), params)
                total_deleted += child_result.rowcount
                logger.info(f"Cascade deleted {child_result.rowcount} records from {child_table}")

        # Delete from main table
        query = f"DELETE FROM {request.table} WHERE {request.id_column} IN ({placeholders})"
        result = await session.execute(text(query), params)
        total_deleted += result.rowcount

        await session.commit()

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin bulk deleted {result.rowcount} records from {request.table}")

        return BulkOperationResult(
            success=True,
            rows_affected=total_deleted,
            execution_time_ms=execution_time_ms,
        )
    except HTTPException:
        raise
    except IntegrityError:
        await session.rollback()
        # Try to find which table has the reference
        fk_tables_result = await session.execute(
            text("""
                SELECT DISTINCT tc.table_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND ccu.table_name = :table_name
                    AND tc.table_schema = 'public'
            """),
            {"table_name": request.table},
        )
        referencing_tables = [row[0] for row in fk_tables_result.all()]

        detail = "Cannot delete: records are referenced by other tables"
        if referencing_tables:
            detail += f" ({', '.join(referencing_tables)})"
        detail += ". Enable 'cascade' option to delete related records first."

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    except Exception as e:
        await session.rollback()
        logger.exception(f"Error in bulk delete: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bulk delete failed: {str(e)}",
        )


@router.post("/bulk/update", response_model=BulkOperationResult)
async def bulk_update(
    request: BulkUpdateRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk update records in a table."""
    import time

    try:
        start_time = time.time()

        # Verify table exists and get column types
        cols_result = await session.execute(
            text("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
            """),
            {"table_name": request.table},
        )

        column_types = {row[0]: row[1] for row in cols_result.all()}
        if not column_types:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{request.table}' not found",
            )

        # Helper to convert value to correct type
        def convert_value(val, col_name):
            if val is None:
                return None
            col_type = column_types.get(col_name, "text").lower()
            if "int" in col_type:
                return int(val) if val != "" else None
            if "bool" in col_type:
                if isinstance(val, bool):
                    return val
                return str(val).lower() in ("true", "1", "yes")
            if "float" in col_type or "double" in col_type or "numeric" in col_type or "decimal" in col_type:
                return float(val) if val != "" else None
            return val

        # Build SET clause with type conversion
        set_parts = []
        params = {}
        for i, (col, val) in enumerate(request.updates.items()):
            set_parts.append(f"{col} = :val_{i}")
            params[f"val_{i}"] = convert_value(val, col)

        # Build WHERE clause with type conversion for IDs
        id_col_type = column_types.get(request.id_column, "text").lower()
        placeholders = ", ".join([f":id_{i}" for i in range(len(request.ids))])
        for i, id_val in enumerate(request.ids):
            if "int" in id_col_type:
                params[f"id_{i}"] = int(id_val)
            else:
                params[f"id_{i}"] = id_val

        query = f"UPDATE {request.table} SET {', '.join(set_parts)} WHERE {request.id_column} IN ({placeholders})"
        result = await session.execute(text(query), params)

        await session.commit()

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin bulk updated {result.rowcount} records in {request.table}")

        return BulkOperationResult(
            success=True,
            rows_affected=result.rowcount,
            execution_time_ms=execution_time_ms,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in bulk update: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bulk update failed: {str(e)}",
        )


# ============================================
# Export Endpoints
# ============================================


@router.get("/tables/{table_name}/export")
async def export_table(
    table_name: str,
    format: Literal["csv", "json", "sql"] = "csv",
    include_schema: bool = True,
    include_data: bool = True,
    limit: int | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Export table data in various formats."""
    try:
        # Verify table exists
        exists_result = await session.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table_name
                )
            """),
            {"table_name": table_name},
        )

        if not exists_result.scalar():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_name}' not found",
            )

        # Get data
        query = f"SELECT * FROM {table_name}"
        if limit:
            query += f" LIMIT {limit}"

        result = await session.execute(text(query))
        rows = result.all()

        if format == "csv":
            output = io.StringIO()
            if rows:
                columns = list(rows[0]._mapping.keys())
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow(serialize_row(row))

            return StreamingResponse(
                io.BytesIO(output.getvalue().encode()),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={table_name}.csv"},
            )

        elif format == "json":
            data = [serialize_row(row) for row in rows]
            return StreamingResponse(
                io.BytesIO(json.dumps(data, indent=2, default=str).encode()),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={table_name}.json"},
            )

        elif format == "sql":
            output = io.StringIO()

            if include_schema:
                # Get column definitions
                cols_result = await session.execute(
                    text("""
                        SELECT
                            column_name,
                            data_type,
                            is_nullable,
                            column_default
                        FROM information_schema.columns
                        WHERE table_name = :table_name AND table_schema = 'public'
                        ORDER BY ordinal_position
                    """),
                    {"table_name": table_name},
                )

                columns_def = []
                for col_row in cols_result.all():
                    col_def = f"    {col_row[0]} {col_row[1]}"
                    if col_row[2] == "NO":
                        col_def += " NOT NULL"
                    if col_row[3]:
                        col_def += f" DEFAULT {col_row[3]}"
                    columns_def.append(col_def)

                output.write(f"-- Table: {table_name}\n")
                output.write(f"-- Exported at: {datetime.now().isoformat()}\n\n")
                output.write(f"CREATE TABLE IF NOT EXISTS {table_name} (\n")
                output.write(",\n".join(columns_def))
                output.write("\n);\n\n")

            if include_data and rows:
                columns = list(rows[0]._mapping.keys())

                for row in rows:
                    values = []
                    row_dict = serialize_row(row)
                    for col in columns:
                        val = row_dict.get(col)
                        if val is None:
                            values.append("NULL")
                        elif isinstance(val, (int, float)):
                            values.append(str(val))
                        elif isinstance(val, bool):
                            values.append("TRUE" if val else "FALSE")
                        else:
                            # Escape single quotes
                            escaped = str(val).replace("'", "''")
                            values.append(f"'{escaped}'")

                    output.write(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(values)});\n")

            return StreamingResponse(
                io.BytesIO(output.getvalue().encode()),
                media_type="application/sql",
                headers={"Content-Disposition": f"attachment; filename={table_name}.sql"},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error exporting table: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}",
        )


# ============================================
# Import Endpoints
# ============================================


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_import(
    file: UploadFile = File(...),
    table: str = Form(...),
    format: Literal["csv", "json", "sql"] = Form("csv"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Preview data to be imported."""
    try:
        # Get table columns
        cols_result = await session.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                ORDER BY ordinal_position
            """),
            {"table_name": table},
        )

        table_columns = [row[0] for row in cols_result.all()]
        if not table_columns:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table}' not found",
            )

        content = await file.read()
        detected_columns = []
        sample_rows = []
        validation_errors = []
        warnings = []

        if format == "csv":
            try:
                text_content = content.decode("utf-8")
                reader = csv.DictReader(io.StringIO(text_content))
                detected_columns = reader.fieldnames or []

                for i, row in enumerate(reader):
                    if i >= 10:  # Preview first 10 rows
                        break
                    sample_rows.append(row)

                total_rows = text_content.count("\n") - 1  # Subtract header
            except Exception as e:
                validation_errors.append(f"CSV parsing error: {str(e)}")
                total_rows = 0

        elif format == "json":
            try:
                data = json.loads(content)
                if isinstance(data, list) and data:
                    detected_columns = list(data[0].keys()) if data[0] else []
                    sample_rows = data[:10]
                    total_rows = len(data)
                else:
                    validation_errors.append("JSON must be an array of objects")
                    total_rows = 0
            except Exception as e:
                validation_errors.append(f"JSON parsing error: {str(e)}")
                total_rows = 0

        elif format == "sql":
            # For SQL files, we just preview the first few lines
            try:
                text_content = content.decode("utf-8")
                lines = text_content.split("\n")
                insert_count = sum(1 for line in lines if line.strip().upper().startswith("INSERT"))
                total_rows = insert_count
                sample_rows = [{"sql_preview": line[:200]} for line in lines[:10] if line.strip()]
                detected_columns = ["sql_statements"]
                warnings.append(f"Found approximately {insert_count} INSERT statements")
            except Exception as e:
                validation_errors.append(f"SQL parsing error: {str(e)}")
                total_rows = 0

        # Auto-map columns
        column_mapping = {}
        for detected in detected_columns:
            # Try exact match first
            if detected in table_columns:
                column_mapping[detected] = detected
            # Try case-insensitive match
            else:
                for table_col in table_columns:
                    if detected.lower() == table_col.lower():
                        column_mapping[detected] = table_col
                        break

        # Check for unmapped columns
        unmapped = [col for col in detected_columns if col not in column_mapping]
        if unmapped and format != "sql":
            warnings.append(f"Unmapped columns: {', '.join(unmapped)}")

        return ImportPreviewResponse(
            total_rows=total_rows,
            sample_rows=sample_rows,
            detected_columns=detected_columns,
            table_columns=table_columns,
            column_mapping=column_mapping,
            validation_errors=validation_errors,
            warnings=warnings,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error previewing import: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preview failed: {str(e)}",
        )


@router.post("/import/execute", response_model=ImportResult)
async def execute_import(
    file: UploadFile = File(...),
    table: str = Form(...),
    format: Literal["csv", "json", "sql"] = Form("csv"),
    mode: Literal["insert", "upsert", "replace"] = Form("insert"),
    column_mapping: str | None = Form(None),  # JSON string
    skip_errors: bool = Form(False),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Execute data import."""
    import time

    try:
        start_time = time.time()

        # Parse column mapping if provided
        col_map = json.loads(column_mapping) if column_mapping else {}

        content = await file.read()
        rows_imported = 0
        rows_updated = 0
        rows_skipped = 0
        errors = []

        if format == "sql":
            # Execute SQL file directly
            try:
                text_content = content.decode("utf-8")
                statements = [s.strip() for s in text_content.split(";") if s.strip()]

                for stmt in statements:
                    if stmt.upper().startswith("INSERT"):
                        try:
                            await session.execute(text(stmt))
                            rows_imported += 1
                        except Exception as e:
                            if skip_errors:
                                errors.append(str(e)[:100])
                                rows_skipped += 1
                            else:
                                raise

                await session.commit()
            except Exception:
                await session.rollback()
                raise

        elif format == "csv":
            text_content = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text_content))

            for row in reader:
                try:
                    # Map columns
                    mapped_row = {}
                    for file_col, value in row.items():
                        db_col = col_map.get(file_col, file_col)
                        if db_col:
                            mapped_row[db_col] = value if value != "" else None

                    if not mapped_row:
                        continue

                    columns = list(mapped_row.keys())
                    placeholders = [f":val_{i}" for i in range(len(columns))]
                    params = {f"val_{i}": v for i, v in enumerate(mapped_row.values())}

                    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
                    await session.execute(text(query), params)
                    rows_imported += 1

                except Exception as e:
                    if skip_errors:
                        errors.append(str(e)[:100])
                        rows_skipped += 1
                    else:
                        await session.rollback()
                        raise

            await session.commit()

        elif format == "json":
            data = json.loads(content)

            for row in data:
                try:
                    # Map columns
                    mapped_row = {}
                    for file_col, value in row.items():
                        db_col = col_map.get(file_col, file_col)
                        if db_col:
                            mapped_row[db_col] = value

                    if not mapped_row:
                        continue

                    columns = list(mapped_row.keys())
                    placeholders = [f":val_{i}" for i in range(len(columns))]
                    params = {f"val_{i}": v for i, v in enumerate(mapped_row.values())}

                    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
                    await session.execute(text(query), params)
                    rows_imported += 1

                except Exception as e:
                    if skip_errors:
                        errors.append(str(e)[:100])
                        rows_skipped += 1
                    else:
                        await session.rollback()
                        raise

            await session.commit()

        execution_time_ms = (time.time() - start_time) * 1000

        logger.info(f"Admin imported {rows_imported} records into {table}")

        return ImportResult(
            success=True,
            rows_imported=rows_imported,
            rows_updated=rows_updated,
            rows_skipped=rows_skipped,
            errors=errors[:50],  # Limit error messages
            execution_time_ms=execution_time_ms,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error executing import: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}",
        )

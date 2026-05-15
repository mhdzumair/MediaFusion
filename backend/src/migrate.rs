//! Database migration runner.
//!
//! Call order at binary startup:
//!   1. `preflight(postgres_uri)` — waits for Postgres, creates the target
//!      database if absent, creates the `pg_trgm` extension.
//!   2. `AppState::build(config)` — builds the connection pool (Postgres is
//!      guaranteed ready at this point).
//!   3. `run(pool)` — runs the Alembic→sqlx bridge then all pending migrations.
//!
//! Alembic bridge: detects an `alembic_version` table and fake-applies the
//! corresponding sqlx migration rows (with compile-time checksums) so
//! `migrator.run()` only applies genuinely outstanding migrations.

use std::time::Duration;

use sqlx::{postgres::PgConnection, Connection, PgPool};
use tracing::{info, warn};
use url::Url;

#[derive(Debug, thiserror::Error)]
pub enum MigrateError {
    #[error("database error: {0}")]
    Db(#[from] sqlx::Error),
    #[error("migration error: {0}")]
    Migration(#[from] sqlx::migrate::MigrateError),
    #[error("invalid postgres URI: {0}")]
    Uri(String),
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Pre-connection setup: wait for Postgres, create the target DB, enable
/// `pg_trgm`.  Must be called before `AppState::build()`.
pub async fn preflight(postgres_uri: &str) -> Result<(), MigrateError> {
    let uri = normalize_uri(postgres_uri);
    let system_uri = system_db_uri(&uri)?;
    let db_name = extract_db_name(&uri)?;

    wait_for_postgres(&system_uri).await?;
    ensure_database(&system_uri, &db_name).await;
    Ok(())
}

/// Run pending migrations.  Bridges from an Alembic-managed database if
/// detected, then runs all outstanding sqlx migrations.
pub async fn run(pool: &PgPool) -> Result<(), MigrateError> {
    let migrator = sqlx::migrate!("./migrations");

    // pg_trgm is required by migration 0002. Create it here so it works on
    // managed databases that don't run postgres-init scripts.
    if let Err(e) = sqlx::query("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        .execute(pool)
        .await
    {
        warn!("could not create pg_trgm extension (may already exist or need superuser): {e}");
    }

    // Alembic bridge: only runs on the very first startup when _sqlx_migrations
    // doesn't exist yet.  Once the table exists (created by the bridge or by
    // sqlx itself) we trust it entirely — re-running the bridge on later
    // startups would undo intentional rollbacks.
    let sqlx_table_exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM pg_tables \
         WHERE schemaname = 'public' AND tablename = '_sqlx_migrations')",
    )
    .fetch_one(pool)
    .await?;

    if !sqlx_table_exists {
        let alembic_exists: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM pg_tables \
             WHERE schemaname = 'public' AND tablename = 'alembic_version')",
        )
        .fetch_one(pool)
        .await?;

        if alembic_exists {
            let rev: Option<String> =
                sqlx::query_scalar("SELECT version_num FROM alembic_version LIMIT 1")
                    .fetch_optional(pool)
                    .await?;

            match rev.as_deref() {
                Some(rev) => match alembic_version_ceiling(rev) {
                    Some(ceiling) => {
                        info!(
                            revision = rev,
                            ceiling, "alembic database — bridging to sqlx"
                        );
                        bridge_alembic(pool, &migrator, ceiling).await?;
                    }
                    None => {
                        warn!(
                            revision = rev,
                            "unknown alembic revision; all sqlx migrations will run \
                             (IF NOT EXISTS guards make this safe)"
                        );
                    }
                },
                None => {
                    info!("alembic_version table is empty — treating as fresh install");
                }
            }
        }
    }

    migrator.run(pool).await?;
    info!("database migrations complete");
    Ok(())
}

/// Roll back to `target_version` (exclusive).  Undoes all migrations with
/// version > `target_version` in reverse order.
///
/// Example: `rollback(pool, 3)` undoes versions 5, 4, leaving 1–3 applied.
pub async fn rollback(pool: &PgPool, target_version: i64) -> Result<(), MigrateError> {
    sqlx::migrate!("./migrations")
        .undo(pool, target_version)
        .await?;
    Ok(())
}

/// Print the applied/pending status of every migration to stdout.
pub async fn status(pool: &PgPool) -> Result<(), MigrateError> {
    let migrator = sqlx::migrate!("./migrations");

    let applied: Vec<(i64, bool)> =
        sqlx::query_as("SELECT version, success FROM _sqlx_migrations ORDER BY version")
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    let applied_map: std::collections::HashMap<i64, bool> = applied.into_iter().collect();

    println!("{:<6} {:<8} Description", "Ver", "Status");
    println!("{}", "-".repeat(50));
    for m in migrator.migrations.iter() {
        if m.migration_type.is_down_migration() {
            continue;
        }
        let status = match applied_map.get(&m.version) {
            Some(true) => "applied",
            Some(false) => "FAILED",
            None => "pending",
        };
        println!("{:<6} {:<8} {}", m.version, status, m.description);
    }
    Ok(())
}

// ── Internal helpers ────────────────────────────────────────────────────────

/// Strip SQLAlchemy driver prefix (`+asyncpg`) so the URI is valid for sqlx.
pub fn normalize_uri(uri: &str) -> String {
    uri.replacen("postgresql+asyncpg://", "postgresql://", 1)
        .replacen("postgres+asyncpg://", "postgres://", 1)
}

/// Replace the database name in `uri` with "postgres" for system-level ops.
fn system_db_uri(uri: &str) -> Result<String, MigrateError> {
    let mut url = Url::parse(uri).map_err(|e| MigrateError::Uri(format!("{uri}: {e}")))?;
    url.set_path("/postgres");
    Ok(url.to_string())
}

/// Extract the database name from a postgres URI.
fn extract_db_name(uri: &str) -> Result<String, MigrateError> {
    let url = Url::parse(uri).map_err(|e| MigrateError::Uri(format!("{uri}: {e}")))?;
    let name = url
        .path()
        .trim_start_matches('/')
        .split('?')
        .next()
        .unwrap_or("mediafusion")
        .to_string();
    if name.is_empty() {
        Ok("mediafusion".to_string())
    } else {
        Ok(name)
    }
}

/// Retry connecting to the system database until Postgres is accepting
/// connections (up to 30 attempts, 2 s apart).
async fn wait_for_postgres(system_uri: &str) -> Result<(), MigrateError> {
    const RETRIES: u32 = 30;
    const INTERVAL: Duration = Duration::from_secs(2);

    info!("waiting for PostgreSQL…");
    for attempt in 1..=RETRIES {
        match PgConnection::connect(system_uri).await {
            Ok(conn) => {
                conn.close().await.ok();
                info!("PostgreSQL is ready");
                return Ok(());
            }
            Err(e) => {
                if attempt == RETRIES {
                    return Err(MigrateError::Db(e));
                }
                warn!(
                    attempt,
                    retries = RETRIES,
                    "postgres not ready, retrying in 2s ({e})"
                );
                tokio::time::sleep(INTERVAL).await;
            }
        }
    }
    unreachable!()
}

/// Create the target database if it doesn't exist.  Errors are logged as
/// warnings and swallowed — managed Postgres instances often disallow this.
async fn ensure_database(system_uri: &str, db_name: &str) {
    let result: Result<(), sqlx::Error> = async {
        let mut conn = PgConnection::connect(system_uri).await?;
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = $1)")
                .bind(db_name)
                .fetch_one(&mut conn)
                .await?;

        if !exists {
            info!(db_name, "creating database");
            // Identifiers cannot be parameterized — sanitize the name.
            let safe = db_name.replace('"', "\"\"");
            sqlx::query(&format!("CREATE DATABASE \"{safe}\""))
                .execute(&mut conn)
                .await?;
        }
        conn.close().await.ok();
        Ok(())
    }
    .await;

    if let Err(e) = result {
        warn!(
            db_name,
            "could not create database (may already exist or insufficient privileges): {e}"
        );
    }
}

/// Map an Alembic revision to the highest sqlx migration version it
/// corresponds to.  Returns `None` for unknown/pre-consolidation revisions.
fn alembic_version_ceiling(rev: &str) -> Option<i64> {
    match rev {
        "d826df80371b" => Some(1), // baseline
        "a1b2c3d4e5f6" => Some(2), // + stream_name trgm index
        "baeb5c5638b8" => Some(3), // + media_id indexes
        "d2f1ac726426" => Some(4), // + no-op annotation queue
        "61c656b49136" => Some(5), // + job queue tables (fully up-to-date)
        _ => None,
    }
}

/// Create `_sqlx_migrations` (if absent) and insert fake rows for all
/// migrations with version ≤ `ceiling`, using real compile-time checksums.
async fn bridge_alembic(
    pool: &PgPool,
    migrator: &sqlx::migrate::Migrator,
    ceiling: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS _sqlx_migrations (
            version        BIGINT      PRIMARY KEY,
            description    TEXT        NOT NULL,
            installed_on   TIMESTAMPTZ NOT NULL DEFAULT now(),
            success        BOOLEAN     NOT NULL,
            checksum       BYTEA       NOT NULL,
            execution_time BIGINT      NOT NULL
        )",
    )
    .execute(pool)
    .await?;

    for migration in migrator.iter() {
        if migration.migration_type.is_down_migration() {
            continue;
        }
        if migration.version > ceiling {
            continue;
        }
        sqlx::query(
            "INSERT INTO _sqlx_migrations
                (version, description, installed_on, success, checksum, execution_time)
             VALUES ($1, $2, now(), true, $3, 0)
             ON CONFLICT (version) DO NOTHING",
        )
        .bind(migration.version)
        .bind(migration.description.as_ref())
        .bind(migration.checksum.as_ref())
        .execute(pool)
        .await?;

        info!(
            version = migration.version,
            description = %migration.description,
            "fake-applied sqlx migration row (alembic bridge)"
        );
    }
    Ok(())
}

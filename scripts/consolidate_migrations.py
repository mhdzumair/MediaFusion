#!/usr/bin/env python3
"""
Script to consolidate Alembic migrations for MediaFusion v5.0 release.

This script helps with:
1. Backing up existing migrations
2. Generating a clean consolidated migration from current models
3. Verifying the new migration against a fresh database

Usage:
    # Step 1: Backup existing migrations
    python scripts/consolidate_migrations.py backup

    # Step 2: Generate clean migration (requires empty database)
    python scripts/consolidate_migrations.py generate --db-url "postgresql+asyncpg://user:pass@localhost:5432/mediafusion_clean"

    # Step 3: Verify the migration works
    python scripts/consolidate_migrations.py verify --db-url "postgresql+asyncpg://user:pass@localhost:5432/mediafusion_test"

    # Step 4: Compare schema (ensure new migration matches current models)
    python scripts/consolidate_migrations.py compare --db-url "postgresql+asyncpg://user:pass@localhost:5432/mediafusion_test"
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="Migration consolidation utilities for MediaFusion v5.0")

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"
BACKUP_DIR = PROJECT_ROOT / "migrations_backup"


def get_migration_files() -> list[Path]:
    """Get all migration files in order."""
    return sorted(
        [f for f in VERSIONS_DIR.glob("*.py") if f.name != "__init__.py"],
        key=lambda x: x.stat().st_mtime,
    )


def _do_backup(output_dir: Optional[Path] = None) -> Path:
    """Internal backup function that does the actual work."""
    backup_path = output_dir or BACKUP_DIR
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_path / f"migrations_{timestamp}"

    typer.echo(f"📦 Backing up migrations to: {backup_path}")

    # Create backup directory
    backup_path.mkdir(parents=True, exist_ok=True)

    # Copy all migration files
    migration_files = get_migration_files()
    for f in migration_files:
        shutil.copy2(f, backup_path / f.name)
        typer.echo(f"  ✓ {f.name}")

    # Also backup env.py and alembic.ini
    shutil.copy2(MIGRATIONS_DIR / "env.py", backup_path / "env.py")
    if (PROJECT_ROOT / "alembic.ini").exists():
        shutil.copy2(PROJECT_ROOT / "alembic.ini", backup_path / "alembic.ini")

    # Create manifest file
    manifest_path = backup_path / "MANIFEST.txt"
    with open(manifest_path, "w") as f:
        f.write("MediaFusion Migration Backup\n")
        f.write(f"Created: {datetime.now().isoformat()}\n")
        f.write(f"Files: {len(migration_files)}\n\n")
        f.write("Migration Chain:\n")
        for mf in migration_files:
            f.write(f"  - {mf.name}\n")

    typer.echo(f"\n✅ Backed up {len(migration_files)} migration files")
    typer.echo(f"📄 Manifest created at: {manifest_path}")
    return backup_path


@app.command()
def backup(
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Custom backup directory"),
):
    """Backup existing migrations before consolidation."""
    _do_backup(output_dir)


@app.command()
def clean(
    keep_backup: bool = typer.Option(True, "--keep-backup/--no-backup", help="Create backup before cleaning"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Remove all existing migrations (creates backup by default)."""
    if not confirm:
        typer.confirm(
            "⚠️  This will remove all existing migration files. Continue?",
            abort=True,
        )

    if keep_backup:
        _do_backup()

    typer.echo("\n🗑️  Cleaning existing migrations...")
    migration_files = get_migration_files()
    for f in migration_files:
        f.unlink()
        typer.echo(f"  ✗ Removed {f.name}")

    # Clean __pycache__
    pycache = VERSIONS_DIR / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
        typer.echo("  ✗ Removed __pycache__")

    typer.echo(f"\n✅ Removed {len(migration_files)} migration files")


@app.command()
def generate(
    db_url: str = typer.Option(
        ...,
        "--db-url",
        "-d",
        help="PostgreSQL URL for clean database (must be empty)",
    ),
    message: str = typer.Option(
        "mediafusion_v5_consolidated_schema",
        "--message",
        "-m",
        help="Migration message",
    ),
):
    """Generate a clean consolidated migration from current models.

    This requires an EMPTY PostgreSQL database. The script will:
    1. Set the database URL temporarily
    2. Run alembic revision --autogenerate
    3. This creates a single migration with the complete schema
    """
    typer.echo("🔧 Generating consolidated migration...")
    typer.echo(f"   Database: {db_url.split('@')[1] if '@' in db_url else db_url}")

    # Set environment variable for the database
    env = os.environ.copy()
    env["POSTGRES_URI"] = db_url

    # Run alembic revision
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "revision",
                "--autogenerate",
                "-m",
                message,
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            typer.echo(f"❌ Failed to generate migration:\n{result.stderr}")
            raise typer.Exit(1)

        typer.echo(result.stdout)
        typer.echo("\n✅ Migration generated successfully!")
        typer.echo("\n📝 Next steps:")
        typer.echo("   1. Review the generated migration in migrations/versions/")
        typer.echo("   2. Run 'python scripts/consolidate_migrations.py verify' to test it")

    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command()
def verify(
    db_url: str = typer.Option(
        ...,
        "--db-url",
        "-d",
        help="PostgreSQL URL for test database (will be modified!)",
    ),
):
    """Verify the migration by applying it to a test database."""
    typer.echo("🧪 Verifying migration...")
    typer.echo(f"   Database: {db_url.split('@')[1] if '@' in db_url else db_url}")

    env = os.environ.copy()
    env["POSTGRES_URI"] = db_url

    # Run alembic upgrade head
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            typer.echo(f"❌ Migration failed:\n{result.stderr}")
            raise typer.Exit(1)

        typer.echo(result.stdout)
        typer.echo("\n✅ Migration applied successfully!")

    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command()
def compare(
    db_url: str = typer.Option(
        ...,
        "--db-url",
        "-d",
        help="PostgreSQL URL for database to compare",
    ),
):
    """Compare database schema against models to check for drift."""
    typer.echo("🔍 Comparing schema against models...")

    env = os.environ.copy()
    env["POSTGRES_URI"] = db_url

    # Run alembic check
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        if "No new upgrade operations detected" in result.stdout or result.returncode == 0:
            typer.echo("\n✅ Schema matches models - no drift detected!")
        else:
            typer.echo(f"\n⚠️  Schema drift detected:\n{result.stdout}\n{result.stderr}")
            typer.echo("\nRun 'alembic revision --autogenerate' to create a migration for the differences")

    except Exception as e:
        typer.echo(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command()
def show_chain():
    """Display the current migration chain."""
    typer.echo("📜 Current Migration Chain:\n")

    migration_files = get_migration_files()

    for f in migration_files:
        content = f.read_text()

        # Extract revision info
        revision = None
        down_revision = None
        for line in content.split("\n"):
            if line.startswith("revision:"):
                revision = line.split("=")[1].strip().strip("\"'")
            elif line.startswith("down_revision:"):
                down_revision = line.split("=")[1].strip().strip("\"'")

        typer.echo(f"  {f.name}")
        typer.echo(f"    revision: {revision}")
        typer.echo(f"    down_revision: {down_revision}")
        typer.echo()


@app.command()
def instructions():
    """Show detailed instructions for migration consolidation."""
    instructions_text = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                  MediaFusion v5.0 Migration Consolidation                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

OVERVIEW
--------
You have 22 migrations that need to be consolidated into a single clean
migration for the v5.0 release.

PREREQUISITES
-------------
1. A separate PostgreSQL database for clean migration generation
2. Access to both MongoDB and PostgreSQL for migration verification

STEP-BY-STEP PROCESS
--------------------

Step 1: Backup existing migrations
    python scripts/consolidate_migrations.py backup

Step 2: Create a clean PostgreSQL database
    # Using psql:
    createdb mediafusion_clean

    # Or using Docker:
    docker run -d --name mf-clean-db \\
        -e POSTGRES_DB=mediafusion_clean \\
        -e POSTGRES_USER=postgres \\
        -e POSTGRES_PASSWORD=postgres \\
        -p 5433:5432 postgres:16

Step 3: Clean existing migrations
    python scripts/consolidate_migrations.py clean --yes

Step 4: Generate clean consolidated migration
    python scripts/consolidate_migrations.py generate \\
        --db-url "postgresql+asyncpg://postgres:postgres@localhost:5433/mediafusion_clean"

Step 5: Review the generated migration
    - Check migrations/versions/ for the new migration file
    - Ensure all tables and indexes are included
    - Add any custom DDL (extensions, triggers) if needed

Step 6: Verify migration works on fresh database
    # Create another test database
    createdb mediafusion_test

    python scripts/consolidate_migrations.py verify \\
        --db-url "postgresql+asyncpg://postgres:postgres@localhost:5433/mediafusion_test"

Step 7: Compare schema to ensure completeness
    python scripts/consolidate_migrations.py compare \\
        --db-url "postgresql+asyncpg://postgres:postgres@localhost:5433/mediafusion_test"

Step 8: Test mongo-to-postgres migration
    python -m migrations.mongo_to_postgres migrate \\
        --mongo-uri "mongodb://localhost:27017/mediafusion" \\
        --postgres-uri "postgresql+asyncpg://postgres:postgres@localhost:5433/mediafusion_test" \\
        --sample 100

    python -m migrations.mongo_to_postgres verify \\
        --mongo-uri "mongodb://localhost:27017/mediafusion" \\
        --postgres-uri "postgresql+asyncpg://postgres:postgres@localhost:5433/mediafusion_test" \\
        --sample 100

CLEANUP
-------
After successful verification:
    # Remove test databases
    dropdb mediafusion_clean
    dropdb mediafusion_test

    # Or stop Docker container
    docker rm -f mf-clean-db

NOTES
-----
- Keep the backup in migrations_backup/ until release is stable
- The consolidated migration should be named with a clear identifier
- Update the migration docstring to indicate it's the v5.0 release schema
"""
    typer.echo(instructions_text)


if __name__ == "__main__":
    app()

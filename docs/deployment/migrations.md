# Database Migrations

MediaFusion uses **sqlx** for database schema migrations. Migrations apply automatically when the API server or worker starts — no manual step required.

## How migrations work

On every startup, MediaFusion checks whether the database schema is up to date and applies any pending migrations before accepting requests. This means:

- First-time startup creates all tables automatically
- Upgrades apply new migrations automatically
- Downgrades require a manual rollback step (see below)

## Checking migration status

Set `MEDIAFUSION_MIGRATE=status` and run either binary. It prints the migration table and exits without starting the server:

=== "Direct binary"

    ```bash
    MEDIAFUSION_MIGRATE=status ./mediafusion-api
    ```

=== "Docker"

    ```bash
    docker run --rm --env-file .env \
      -e MEDIAFUSION_MIGRATE=status \
      mhdzumair/mediafusion:6.0.0
    ```

=== "Docker Compose"

    ```bash
    docker compose -f docker-compose.yml run --rm \
      -e MEDIAFUSION_MIGRATE=status \
      mediafusion
    ```

## Rolling back migrations

!!! warning "Required before downgrading"
    If you ran a newer version that applied new migrations, you **must** roll back before switching to an older image. The older binary will refuse to start if it finds schema versions it doesn't recognize.

Set `MEDIAFUSION_MIGRATE_ROLLBACK_TO=<version>` to roll back to a specific version:

=== "Direct binary"

    ```bash
    # Roll back to version 4
    MEDIAFUSION_MIGRATE_ROLLBACK_TO=4 ./mediafusion-api
    ```

=== "Docker"

    ```bash
    docker run --rm --env-file .env \
      -e MEDIAFUSION_MIGRATE_ROLLBACK_TO=4 \
      mhdzumair/mediafusion:6.0.0
    ```

## Downgrading from 6.x to 5.x

Version 5.x used **Alembic** for migrations. After rolling back with the 6.x binary, you must restore the Alembic version marker:

1. Roll back with the 6.x binary to the correct version (check the 5.x release notes for the target version number).
2. Connect to PostgreSQL and run:

    ```sql
    UPDATE alembic_version SET version_num = 'd826df80371b';
    ```

3. Start the 5.x binary — it will see the correct Alembic revision and start normally.

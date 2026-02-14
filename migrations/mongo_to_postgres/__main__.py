"""Entry point for running as a module: python -m migrations.mongo_to_postgres"""

from migrations.mongo_to_postgres.cli import app

if __name__ == "__main__":
    app()

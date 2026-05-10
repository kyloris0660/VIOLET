#!/usr/bin/env python3
"""Idempotent setup for the V.I.O.L.E.T. test database.

Usage:
    python scripts/setup_test_db.py
    python scripts/setup_test_db.py --dry-run
    python scripts/setup_test_db.py --migrate
    python scripts/setup_test_db.py --migrate --dry-run

Creates the 'blombooru_test' database on localhost:5432 if it does not exist.
With --migrate, also runs the app's schema initialization (create_all + migrations).

Requires psycopg2 and a running PostgreSQL instance with the 'postgres' superuser.
"""
import argparse
import os
import sys

_FORBIDDEN_DB_NAMES = frozenset({"blombooru", "production", "main", "postgres"})


def _get_test_db_name():
    test_db = os.getenv("POSTGRES_DB", "blombooru_test")
    if test_db.lower() in _FORBIDDEN_DB_NAMES:
        print(f"ERROR: POSTGRES_DB is '{test_db}' — forbidden for test use. "
              f"Set it to a test-specific name like 'blombooru_test'.")
        sys.exit(1)
    return test_db


def _create_database(test_db, dry_run):
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    except ImportError:
        print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary")
        sys.exit(1)

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")

    print(f"Connecting to PostgreSQL at {host}:{port} as '{user}' ...")
    conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,))
    exists = cur.fetchone()

    if dry_run:
        if exists:
            print(f"[dry-run] Database '{test_db}' exists.")
        else:
            print(f"[dry-run] Database '{test_db}' does NOT exist — would create it.")
    elif exists:
        print(f"Database '{test_db}' already exists — nothing to do.")
    else:
        cur.execute(f'CREATE DATABASE "{test_db}"')
        print(f"Database '{test_db}' created successfully.")

    cur.close()
    conn.close()
    return bool(exists) or not dry_run


def _build_migrate_url(test_db):
    """Build an explicit SQLAlchemy URL from env vars and the validated test_db name.

    This bypasses app config / .env loading entirely, so a local .env containing
    POSTGRES_DB=blombooru can never redirect the migration target.
    """
    from sqlalchemy.engine import URL

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")

    if test_db.lower() in _FORBIDDEN_DB_NAMES:
        print(f"ERROR: target DB is '{test_db}' — forbidden for migration.")
        sys.exit(1)

    return URL.create(
        drivername="postgresql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=test_db,
    )


def _run_migrate(test_db, dry_run):
    """Run app schema initialization against the test database."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backend_dir = os.path.join(project_root, "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    os.environ.setdefault("VIOLET_ENV", "test")
    if os.environ.get("VIOLET_ENV") != "test":
        print(f"ERROR: VIOLET_ENV is '{os.environ['VIOLET_ENV']}', expected 'test'. "
              "Refusing to run --migrate outside test environment.")
        sys.exit(1)

    if dry_run:
        print(f"[dry-run] Would run schema migration on '{test_db}'.")
        return

    url = _build_migrate_url(test_db)
    print(f"Running schema initialization on '{test_db}' at {url.host}:{url.port} ...")

    from sqlalchemy import create_engine
    from app.database import Base, check_and_migrate_schema
    from app import models  # noqa: F401 — registers all models with Base.metadata

    migrate_engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )

    Base.metadata.create_all(bind=migrate_engine)
    print("  Tables created (idempotent).")

    check_and_migrate_schema(migrate_engine)
    print("  Migrations applied (idempotent).")

    migrate_engine.dispose()
    print(f"Schema initialization complete on '{test_db}'.")


def main():
    parser = argparse.ArgumentParser(description="Set up V.I.O.L.E.T. test database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check state without making changes")
    parser.add_argument("--migrate", action="store_true",
                        help="Run app schema initialization after DB creation")
    args = parser.parse_args()

    test_db = _get_test_db_name()
    _create_database(test_db, args.dry_run)

    if args.migrate:
        _run_migrate(test_db, args.dry_run)

    print("Done.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Idempotent setup for the V.I.O.L.E.T. test database.

Usage:
    python scripts/setup_test_db.py

Creates the 'blombooru_test' database on localhost:5432 if it does not exist.
Requires psycopg2 and a running PostgreSQL instance with the 'postgres' superuser.
"""
import os
import sys

def main():
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
    test_db = os.getenv("POSTGRES_DB", "blombooru_test")

    if test_db == "blombooru":
        print("ERROR: POSTGRES_DB is 'blombooru' (production default). "
              "Set it to a test-specific name like 'blombooru_test'.")
        sys.exit(1)

    print(f"Connecting to PostgreSQL at {host}:{port} as '{user}' ...")
    conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,))
    exists = cur.fetchone()

    if exists:
        print(f"Database '{test_db}' already exists — nothing to do.")
    else:
        cur.execute(f'CREATE DATABASE "{test_db}"')
        print(f"Database '{test_db}' created successfully.")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()

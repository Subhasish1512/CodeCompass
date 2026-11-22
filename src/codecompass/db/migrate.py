"""
Tiny migration runner for CodeCompass.

Creates a schema_migrations table and runs .sql files from migrations/
in alphabetical order. Each migration is idempotent (uses IF NOT EXISTS,
CREATE OR REPLACE, etc.) and recorded by filename.
"""

import sys
from pathlib import Path
from typing import List, Set

import psycopg
from psycopg.rows import dict_row

from codecompass.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


def get_connection():
    """Get a database connection."""
    settings = get_settings()
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=False)


def ensure_migrations_table(conn) -> None:
    """Create schema_migrations table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


def get_applied_migrations(conn) -> Set[str]:
    """Get set of already-applied migration filenames."""
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations ORDER BY filename")
        return {row["filename"] for row in cur.fetchall()}


def get_migration_files() -> List[Path]:
    """Get all .sql migration files in order."""
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migration(conn, migration_file: Path) -> None:
    """Execute a single migration file."""
    sql = migration_file.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    # Record migration
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
            (migration_file.name,)
        )
    conn.commit()
    print(f"Applied: {migration_file.name}")


def migrate() -> None:
    """Run all pending migrations."""
    conn = get_connection()
    try:
        ensure_migrations_table(conn)
        applied = get_applied_migrations(conn)
        migrations = get_migration_files()

        if not migrations:
            print("No migration files found")
            return

        pending = [m for m in migrations if m.name not in applied]

        if not pending:
            print("All migrations already applied")
            return

        print(f"Running {len(pending)} pending migration(s)...")
        for mf in pending:
            run_migration(conn, mf)
        print("Migrations complete")
    finally:
        conn.close()


def status() -> None:
    """Show migration status."""
    conn = get_connection()
    try:
        ensure_migrations_table(conn)
        applied = get_applied_migrations(conn)
        migrations = get_migration_files()

        print("Migration Status:")
        print("-" * 50)
        for mf in migrations:
            status_str = "✓ applied" if mf.name in applied else "✗ pending"
            print(f"  {mf.name:<40} {status_str}")
        if not migrations:
            print("  (no migration files found)")
    finally:
        conn.close()


def reset() -> None:
    """Drop all tables and re-run migrations (DANGEROUS)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Drop all tables in dependency order
            cur.execute("""
                DROP TABLE IF EXISTS chunks CASCADE;
                DROP TABLE IF EXISTS edges CASCADE;
                DROP TABLE IF EXISTS symbols CASCADE;
                DROP TABLE IF EXISTS files CASCADE;
                DROP TABLE IF EXISTS repos CASCADE;
                DROP TABLE IF EXISTS schema_migrations CASCADE;
            """)
        conn.commit()
        print("All tables dropped")
    finally:
        conn.close()
    # Re-run migrations
    migrate()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m codecompass.db.migrate [migrate|status|reset]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "migrate":
        migrate()
    elif cmd == "status":
        status()
    elif cmd == "reset":
        reset()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
"""
Shared database connection module.
All modules import: from infra.db import get_connection, execute_query
"""
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from pathlib import Path
import sys

# Allow running from any module directory
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from infra.config import DB_CONFIG
except ImportError:
    raise RuntimeError(
        "infra/config.py not found. Copy infra/config.example.py to infra/config.py and fill in credentials."
    )


@contextmanager
def get_connection():
    """Context manager for DB connection. Auto-commits and closes."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_query(sql: str, params=None, fetch: str = "all"):
    """
    Execute a query and return results.
    fetch: 'all' | 'one' | 'none'
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "all":
                return cur.fetchall()
            elif fetch == "one":
                return cur.fetchone()
            else:
                return None


def insert_returning(sql: str, params=None) -> dict:
    """Execute INSERT ... RETURNING and return the row."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return dict(cur.fetchone())


def bulk_insert(table: str, rows: list[dict]) -> int:
    """Bulk insert list of dicts into table. Returns count inserted."""
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join(["%s"] * len(columns))
    col_str = ",".join(columns)
    sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    with get_connection() as conn:
        with conn.cursor() as cur:
            data = [[row[c] for c in columns] for row in rows]
            psycopg2.extras.execute_batch(cur, sql, data, page_size=500)
            return cur.rowcount

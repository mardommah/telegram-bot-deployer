"""
Auto-migration for SQLite.

Runs on app startup:
1. Checks table integrity (PK, constraints) — rebuilds broken tables
2. Adds missing columns via ALTER TABLE
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from app.database import Base

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "VARCHAR": "VARCHAR",
    "STRING": "VARCHAR",
    "TEXT": "TEXT",
    "INTEGER": "INTEGER",
    "FLOAT": "REAL",
    "BOOLEAN": "INTEGER",
    "DATETIME": "DATETIME",
    "DATE": "DATE",
    "NUMERIC": "NUMERIC",
}


def _sql_type(column) -> str:
    type_name = type(column.type).__name__.upper()
    sql_type = _TYPE_MAP.get(type_name, "TEXT")
    if sql_type == "VARCHAR" and hasattr(column.type, "length") and column.type.length:
        sql_type = f"VARCHAR({column.type.length})"
    return sql_type


def _default_clause(column) -> str:
    if column.server_default is not None:
        return f" DEFAULT {column.server_default.arg}"
    if column.default is not None and column.default.is_scalar:
        val = column.default.arg
        if isinstance(val, str):
            escaped = val.replace("'", "''")
            return f" DEFAULT '{escaped}'"
        return f" DEFAULT {val}"
    if column.nullable:
        return " DEFAULT NULL"
    return " DEFAULT ''"


def _check_table_integrity(conn: Connection, table_name: str, table) -> bool:
    """Check if table has correct PK and basic structure. Returns False if broken."""
    result = conn.execute(text(f"PRAGMA table_info('{table_name}')"))
    columns = result.fetchall()

    if not columns:
        return True  # Table doesn't exist, create_all handles it

    # Check if PK column has pk flag set (column[5] is pk flag in PRAGMA table_info)
    pk_cols = {col.name for col in table.columns if col.primary_key}
    for col_row in columns:
        col_name = col_row[1]
        is_pk = col_row[5]
        if col_name in pk_cols and not is_pk:
            return False  # PK column missing primary key constraint

    return True


def _rebuild_table(conn: Connection, table_name: str, table):
    """Rebuild a broken table by migrating data through a temp table."""
    logger.warning(f"[migration] Table '{table_name}' has broken schema — rebuilding")

    # Get existing column names
    result = conn.execute(text(f"PRAGMA table_info('{table_name}')"))
    existing_cols = [row[1] for row in result.fetchall()]

    # Find columns that exist in both old table and new model
    model_cols = [col.name for col in table.columns]
    common_cols = [c for c in model_cols if c in existing_cols]

    if not common_cols:
        # No data to preserve, just drop and let create_all handle it
        conn.execute(text(f"DROP TABLE {table_name}"))
        logger.info(f"[migration] Dropped empty/incompatible table '{table_name}'")
        return

    cols_str = ", ".join(common_cols)

    conn.execute(text(f"ALTER TABLE {table_name} RENAME TO __{table_name}_broken"))
    # create_all will make the new table after this function returns
    # But we need to create it now to copy data
    table.create(conn)
    conn.execute(text(f"INSERT INTO {table_name} ({cols_str}) SELECT {cols_str} FROM __{table_name}_broken"))
    conn.execute(text(f"DROP TABLE __{table_name}_broken"))
    conn.commit()
    logger.info(f"[migration] Rebuilt table '{table_name}' with {cols_str}")


def run_migrations(conn: Connection):
    """Compare models to DB schema, fix integrity issues, add missing columns."""
    inspector = inspect(conn)
    migrated = 0

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue

        # Step 1: Check integrity
        if not _check_table_integrity(conn, table_name, table):
            _rebuild_table(conn, table_name, table)
            continue  # Table is now fresh, no need to check columns

        # Step 2: Add missing columns
        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing_columns:
                continue

            sql_type = _sql_type(column)
            default = _default_clause(column)
            nullable = "" if column.nullable else " NOT NULL"

            if nullable and not default:
                default = " DEFAULT ''"

            stmt = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {sql_type}{nullable}{default}"
            logger.info(f"[migration] {stmt}")
            conn.execute(text(stmt))
            migrated += 1

    if migrated:
        conn.commit()
        logger.info(f"[migration] Added {migrated} column(s)")
    else:
        logger.info("[migration] Database schema is up to date")

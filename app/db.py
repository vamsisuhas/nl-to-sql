"""
Schema introspection + query execution.

Two backends:
  - DuckDB: for CSVs and the demo data. In-process, no setup.
  - Postgres: for live database mode via a connection string.

Both expose the same interface: get_schema_text() returns a string suitable
for inclusion in the system prompt, and run_query() executes a (already-
validated) SELECT and returns rows + column names.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Sequence

import duckdb
import psycopg


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool = False


class Database:
    """Common interface — subclassed by the two backends below."""

    def get_schema_text(self) -> str:
        raise NotImplementedError

    def run_query(self, sql: str) -> QueryResult:
        raise NotImplementedError


class DuckDBDatabase(Database):
    """In-process DuckDB. Used for CSV-based demos."""

    def __init__(self, csv_paths: dict[str, str]) -> None:
        """csv_paths maps table_name -> path/to/file.csv."""
        self.conn = duckdb.connect()
        for table, path in csv_paths.items():
            self.conn.execute(
                f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?)",
                [path],
            )
        self._tables = list(csv_paths.keys())

    def get_schema_text(self) -> str:
        lines: list[str] = []
        for table in self._tables:
            rows = self.conn.execute(f"DESCRIBE {table}").fetchall()
            cols = ", ".join(f"{name} {dtype}" for name, dtype, *_ in rows)
            lines.append(f"TABLE {table} ({cols})")
        return "\n".join(lines)

    def run_query(self, sql: str) -> QueryResult:
        cur = self.conn.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return QueryResult(columns=columns, rows=[list(r) for r in rows])


class PostgresDatabase(Database):
    """Live Postgres via a connection string."""

    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo
        # Open once to validate; close immediately.
        with psycopg.connect(conninfo) as _:
            pass

    def get_schema_text(self) -> str:
        with psycopg.connect(self.conninfo) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name, ordinal_position
                """
            )
            rows = cur.fetchall()

        # Group by table.
        by_table: dict[str, list[tuple[str, str]]] = {}
        for schema, table, col, dtype in rows:
            key = f"{schema}.{table}" if schema != "public" else table
            by_table.setdefault(key, []).append((col, dtype))

        lines: list[str] = []
        for table, cols in by_table.items():
            col_str = ", ".join(f"{c} {t}" for c, t in cols)
            lines.append(f"TABLE {table} ({col_str})")
        return "\n".join(lines)

    def run_query(self, sql: str) -> QueryResult:
        with psycopg.connect(self.conninfo) as conn, conn.cursor() as cur:
            cur.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall()
        return QueryResult(columns=columns, rows=[list(r) for r in rows])


def load_sample() -> Database:
    """Load the bundled demo dataset."""
    here = os.path.dirname(__file__)
    csv_path = os.path.abspath(os.path.join(here, "..", "sample-data", "fleet_events.csv"))
    return DuckDBDatabase({"fleet_events": csv_path})

"""
Safety-validator tests. These are the contract for "this query will not modify data."

The tests are pessimistic — anything that's ambiguous gets rejected. We'd rather
say no to a valid SELECT than yes to a sneaky write.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety import validate_and_safe_sql


def test_simple_select_passes():
    r = validate_and_safe_sql("SELECT id, name FROM users")
    assert r.ok
    assert "LIMIT" in r.sql.upper()


def test_existing_limit_preserved():
    r = validate_and_safe_sql("SELECT * FROM events LIMIT 5")
    assert r.ok
    assert "LIMIT 5" in r.sql.upper()


def test_with_query_passes():
    r = validate_and_safe_sql("WITH t AS (SELECT 1) SELECT * FROM t")
    assert r.ok


def test_rejects_insert():
    r = validate_and_safe_sql("INSERT INTO users (name) VALUES ('x')")
    assert not r.ok
    assert "INSERT" in r.reason


def test_rejects_update():
    r = validate_and_safe_sql("UPDATE users SET name = 'x' WHERE id = 1")
    assert not r.ok


def test_rejects_delete():
    r = validate_and_safe_sql("DELETE FROM users")
    assert not r.ok


def test_rejects_drop():
    r = validate_and_safe_sql("DROP TABLE users")
    assert not r.ok


def test_rejects_truncate():
    r = validate_and_safe_sql("TRUNCATE TABLE users")
    assert not r.ok


def test_rejects_alter():
    r = validate_and_safe_sql("ALTER TABLE users ADD COLUMN x INT")
    assert not r.ok


def test_rejects_stacked_statements():
    r = validate_and_safe_sql("SELECT 1; DELETE FROM users")
    assert not r.ok


def test_rejects_write_inside_with():
    # WITH ... cannot front a DELETE in standard SQL but some dialects allow it.
    # The validator should still reject because of the DELETE token.
    r = validate_and_safe_sql("WITH x AS (SELECT 1) DELETE FROM users")
    assert not r.ok


def test_rejects_empty():
    r = validate_and_safe_sql("")
    assert not r.ok


def test_rejects_whitespace_only():
    r = validate_and_safe_sql("   \n\t  ")
    assert not r.ok


def test_strips_markdown_fence():
    r = validate_and_safe_sql("```sql\nSELECT 1\n```")
    assert r.ok
    assert "SELECT" in r.sql.upper()


def test_rejects_create():
    r = validate_and_safe_sql("CREATE TABLE x (id INT)")
    assert not r.ok


def test_rejects_grant():
    r = validate_and_safe_sql("GRANT ALL ON users TO public")
    assert not r.ok


def test_rejects_call():
    r = validate_and_safe_sql("CALL maintenance_procedure()")
    assert not r.ok


def test_rejects_copy():
    r = validate_and_safe_sql("COPY users FROM '/tmp/x.csv'")
    assert not r.ok


def test_complex_query_with_joins_and_aggregates_passes():
    sql = """
    SELECT u.country, COUNT(*) AS n, AVG(o.amount) AS avg_amt
    FROM users u
    JOIN orders o ON o.user_id = u.id
    WHERE o.created_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY u.country
    HAVING COUNT(*) > 10
    ORDER BY n DESC
    """
    r = validate_and_safe_sql(sql)
    assert r.ok


def test_window_function_passes():
    sql = """
    SELECT id, name, RANK() OVER (PARTITION BY country ORDER BY signup_date) AS r
    FROM users
    """
    r = validate_and_safe_sql(sql)
    assert r.ok

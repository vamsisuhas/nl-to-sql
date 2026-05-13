"""
SQL safety validator.

Generated SQL is run against a real database — so before execution, parse it
with sqlparse and assert it's a pure read:

  - Single statement only (no `;`-stacked statements)
  - No write keywords anywhere (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE,
    TRUNCATE, GRANT, REVOKE, MERGE, CALL, COPY, EXPLAIN ANALYZE with side
    effects)
  - The first non-whitespace token must be SELECT or WITH
  - A LIMIT is auto-injected if absent (caps row count regardless of intent)

This module is intentionally pessimistic: anything ambiguous gets rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlparse
from sqlparse.tokens import DML, DDL, Keyword


WRITE_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "MERGE",
    "CALL",
    "COPY",
    "REPLACE",
    "RENAME",
    "VACUUM",
    "ANALYZE",
    "EXEC",
    "EXECUTE",
    "ATTACH",
    "DETACH",
}

DEFAULT_LIMIT = 1000


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    sql: str = ""


def validate_and_safe_sql(raw_sql: str, max_rows: int = DEFAULT_LIMIT) -> ValidationResult:
    """Returns ValidationResult.ok=True with the safe-to-run SQL, or ok=False with a reason."""
    if not raw_sql or not raw_sql.strip():
        return ValidationResult(ok=False, reason="empty SQL")

    # Strip any markdown fences in case the model snuck them in.
    cleaned = _strip_fences(raw_sql).strip().rstrip(";")

    parsed = sqlparse.parse(cleaned)
    if len(parsed) == 0:
        return ValidationResult(ok=False, reason="could not parse SQL")
    if len(parsed) > 1:
        return ValidationResult(ok=False, reason="multiple statements are not allowed")

    statement = parsed[0]

    # Walk every token (including nested ones) and reject any write keyword.
    for token in statement.flatten():
        if token.ttype in (DML, DDL, Keyword):
            up = token.value.upper()
            if up in WRITE_KEYWORDS:
                return ValidationResult(
                    ok=False, reason=f"disallowed keyword found: {up}"
                )

    # The first significant token must be SELECT or WITH.
    first = _first_significant_keyword(statement)
    if first not in ("SELECT", "WITH"):
        return ValidationResult(
            ok=False, reason=f"only SELECT/WITH queries are allowed (got {first})"
        )

    safe_sql = _ensure_limit(cleaned, max_rows)
    return ValidationResult(ok=True, sql=safe_sql)


def _strip_fences(sql: str) -> str:
    """Remove ```sql ... ``` fences if a model output snuck them in."""
    s = sql.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:sql)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def _first_significant_keyword(statement) -> str:
    """Return the first SELECT/WITH/whatever keyword in the statement."""
    for token in statement.tokens:
        if token.is_whitespace:
            continue
        if hasattr(token, "ttype") and token.ttype in (DML, Keyword):
            return token.value.upper()
        # Tokens like Identifier wrap their first child; descend.
        if hasattr(token, "tokens"):
            for inner in token.flatten():
                if inner.ttype in (DML, Keyword):
                    return inner.value.upper()
        # Fall back to first non-whitespace token's value.
        return str(token).strip().split()[0].upper()
    return ""


# A LIMIT can be at the top of the statement or inside a UNION; this is intentionally
# simple — if there's no LIMIT keyword anywhere, append one. If there is, leave it
# alone (the user/model already chose a cap).
_LIMIT_RX = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


def _ensure_limit(sql: str, max_rows: int) -> str:
    if _LIMIT_RX.search(sql):
        return sql
    return f"{sql.rstrip()} LIMIT {max_rows}"

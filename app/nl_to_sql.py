"""
Natural-language to SQL via OpenAI function calling.

Returns a structured object — never free text — so downstream parsing is
deterministic. The function schema forces the model to fill in:

  - sql:         the query to run
  - explanation: one-sentence plain-English description of what the query does
  - columns_used: list of column names referenced (for UI highlighting)

We pass the database schema in the system prompt so the model produces SQL
that compiles against the real tables, not a hallucinated one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from openai import OpenAI


@dataclass
class NlToSqlResponse:
    sql: str
    explanation: str
    columns_used: list[str]


SYSTEM_PROMPT_TEMPLATE = """You translate analyst questions into SQL.

The database is read-only. You may use SELECT and WITH queries only.
Never produce INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or TRUNCATE.

When in doubt about a column or table that isn't in the schema, ask the user a
clarifying question by setting `sql` to empty string and explaining what's
missing in `explanation`.

The schema is:

{schema}

Generate SQL that runs on this schema. Prefer:
  - explicit column lists over SELECT *
  - LIMIT 1000 unless the user clearly wants more
  - readable formatting (one clause per line)
"""


_FUNCTION_SPEC = {
    "type": "function",
    "function": {
        "name": "answer_with_sql",
        "description": "Answer the user's question by producing SQL that runs against the database.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SELECT/WITH SQL query to run. Empty string if the question cannot be answered with the available schema.",
                },
                "explanation": {
                    "type": "string",
                    "description": "One sentence in plain English describing what the SQL does.",
                },
                "columns_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names the query references.",
                },
            },
            "required": ["sql", "explanation", "columns_used"],
        },
    },
}


def translate(
    question: str,
    schema_text: str,
    *,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> NlToSqlResponse:
    """Call OpenAI and return the parsed structured response.

    api_key: if provided, used directly (preferred — supplied per-request from the
    UI so the server doesn't need a key configured). Falls back to OPENAI_API_KEY
    env var if not provided.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "no OpenAI API key — pass one in the UI or set OPENAI_API_KEY on the server"
        )
    client = OpenAI(api_key=key)

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text)},
            {"role": "user", "content": question},
        ],
        tools=[_FUNCTION_SPEC],
        tool_choice={"type": "function", "function": {"name": "answer_with_sql"}},
        temperature=0,
    )

    tool_call = completion.choices[0].message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    return NlToSqlResponse(
        sql=args["sql"],
        explanation=args["explanation"],
        columns_used=args.get("columns_used", []),
    )

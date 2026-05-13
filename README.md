# NL-to-SQL Analytics Tool

A natural-language query interface for SQL databases. You point it at a Postgres connection or upload a CSV, then ask questions in plain English — the tool uses OpenAI function calling to translate to SQL, validates the SQL is read-only, runs it, and shows the result.

```
   ┌────────────┐    POST /ask   ┌────────────────────┐
   │            │ ──────────────▶│                    │
   │  Browser   │                │   FastAPI server   │
   │            │ ◀────────────  │                    │
   └────────────┘   result rows  └─────────┬──────────┘
                                           │
                          ┌────────────────┼─────────────────────┐
                          ▼                ▼                     ▼
                ┌──────────────────┐  ┌─────────────┐   ┌──────────────────┐
                │  Schema fetcher  │  │  OpenAI     │   │   SQL safety     │
                │  (Postgres/      │  │  function   │   │   validator      │
                │   DuckDB)        │  │  calling    │   │ (read-only,      │
                └──────────────────┘  └─────────────┘   │  LIMIT enforced) │
                                                       └──────────────────┘
```

## What this is

Most "AI for SQL" demos generate any SQL the model wants and run it directly — which means a wrong-looking question can lead to a table scan over a 100 GB table, or worse, a `DROP TABLE`. This tool puts three things in the way:

1. **Schema-grounded prompting** — the system prompt always includes the actual schema (table names, column names + types, primary keys, foreign keys), so the model produces SQL that compiles against the real database, not a hallucinated one.
2. **Safety validation** — generated SQL is parsed with `sqlparse` and rejected if it contains anything other than `SELECT` / `WITH`. A `LIMIT` is auto-injected if absent.
3. **Structured outputs** — the model returns `{ sql, explanation, columns_used }` via OpenAI's response-format schema, not free text — so the response is machine-checkable, not regex-fragile.

The result is a tool that an analyst or PM can use safely without writing SQL, while a backend engineer can trust the generated query will never modify data.

## Stack

| Component       | What it does                                                       |
|-----------------|--------------------------------------------------------------------|
| **FastAPI**     | HTTP API server                                                    |
| **OpenAI API**  | `gpt-4o-mini` with function calling + structured outputs           |
| **DuckDB**      | In-process SQL engine (works against CSVs or Postgres adapters)    |
| **psycopg**     | Postgres adapter for live database mode                            |
| **sqlparse**    | SQL parser used by the safety validator                            |
| **HTMX**        | Frontend (no React build step — single static HTML file)           |

## Try the live demo

The hosted version runs without a server-side OpenAI key — visitors bring their own. Paste your OpenAI API key into the UI, load the sample dataset, and ask anything. Your key is stored only in your browser's localStorage and forwarded once-per-request to OpenAI; the server never persists it.

## Quick start (local)

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run (no env var needed — paste your key into the UI)
uvicorn app.main:app --reload --port 8000

# 3. Open
open http://localhost:8000
```

The home page lets you load the sample dataset (a CSV of fleet telemetry events) or paste a Postgres connection string. Paste your OpenAI key into the UI, then ask anything in the input box.

If you'd rather configure the key server-side, set `OPENAI_API_KEY` before running uvicorn — the app falls back to it when no key is supplied per-request.

## Example questions

Against the sample dataset:

- *"How many events did robot-001 emit today?"* → `SELECT COUNT(*) FROM fleet_events WHERE robot_id = 'robot-001' AND created_at >= CURRENT_DATE LIMIT 1000`
- *"What's the 30-day retention by mission type?"* → multi-step CTE with `LAG` window function
- *"Which robots ran low on battery yesterday?"* → joins on the `battery_pct` payload field

The "Explain" button shows the SQL the model produced and a one-line natural-language explanation of how it answers your question.

## Architecture decisions

### Why function calling instead of free-text SQL extraction

Free-text SQL extraction (the typical `INSERT YOUR SCHEMA HERE; ANSWER THE QUESTION: {q}; OUTPUT SQL:` pattern) requires regex parsing of the model output and breaks the moment the model adds a markdown fence, a leading sentence, or wraps the SQL in a `WITH` it didn't have to. Function calling guarantees the model returns a JSON object with named fields the application code can rely on.

### Why a separate safety validator instead of trusting the model

The model is trained not to generate destructive SQL when asked nicely — but the application's contract is *"never modify data"*, which is a stronger guarantee than *"the model usually behaves"*. The safety validator parses the SQL with `sqlparse`, walks the token tree, and rejects anything that isn't a pure read. The validator has unit tests in `tests/test_safety.py` covering injection attempts via comments, stacked statements, `UPDATE` disguised as `WITH`, and similar bypass patterns.

### Why DuckDB + Postgres

DuckDB lets the tool run against a CSV without setup (great for demos and ad-hoc analysis). For "real" databases, the same query path works against Postgres via the `postgres_scanner` extension or via `psycopg` directly. Both paths share the same safety validator and prompt format.

## Project layout

```
nl-to-sql/
├── app/
│   ├── main.py          # FastAPI app + routes
│   ├── nl_to_sql.py     # OpenAI function-calling prompt + response parsing
│   ├── db.py            # Schema fetch + query execution (DuckDB / Postgres)
│   └── safety.py        # SQL safety validator (read-only + LIMIT enforcement)
├── static/
│   └── index.html       # HTMX single-page frontend
├── sample-data/
│   └── fleet_events.csv # Demo dataset
├── tests/
│   └── test_safety.py   # Unit tests for the safety validator
├── requirements.txt
├── Dockerfile           # For Fly.io / Railway deploy
├── fly.toml             # Fly.io app config
├── .github/workflows/ci.yml
└── README.md
```

## Deploy

The included `fly.toml` and `Dockerfile` make `fly launch && fly deploy` work out of the box. The only required runtime secret is `OPENAI_API_KEY`:

```bash
fly secrets set OPENAI_API_KEY=sk-...
fly deploy
```

## License

MIT.

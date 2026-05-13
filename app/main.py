"""
FastAPI app entry point.

Routes:
  GET  /            -> static HTMX frontend
  POST /api/connect -> load the sample dataset or a Postgres connection string
  POST /api/ask     -> NL question; returns SQL, explanation, and result rows
  GET  /api/schema  -> current schema text
  GET  /healthz     -> liveness probe
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, nl_to_sql, safety


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nl-to-sql")

app = FastAPI(title="NL-to-SQL Analytics Tool")

# In-process database handle. Kept simple for the demo — production would
# scope this per-user/session and store connection strings encrypted at rest.
_state: dict[str, Optional[db.Database]] = {"db": None}


@app.get("/", response_class=HTMLResponse)
def root():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "static", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/healthz")
def healthz():
    return {"ok": True, "connected": _state["db"] is not None}


class ConnectRequest(BaseModel):
    mode: str  # "sample" or "postgres"
    conninfo: Optional[str] = None


@app.post("/api/connect")
def connect(req: ConnectRequest):
    if req.mode == "sample":
        _state["db"] = db.load_sample()
    elif req.mode == "postgres":
        if not req.conninfo:
            raise HTTPException(400, "conninfo is required for postgres mode")
        try:
            _state["db"] = db.PostgresDatabase(req.conninfo)
        except Exception as e:
            raise HTTPException(400, f"could not connect: {e}")
    else:
        raise HTTPException(400, f"unknown mode: {req.mode}")

    return {"ok": True, "schema": _state["db"].get_schema_text()}


@app.get("/api/schema")
def get_schema():
    if _state["db"] is None:
        raise HTTPException(400, "no database connected")
    return {"schema": _state["db"].get_schema_text()}


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
def ask(req: AskRequest):
    if _state["db"] is None:
        raise HTTPException(400, "no database connected")
    if not req.question.strip():
        raise HTTPException(400, "question is empty")

    schema_text = _state["db"].get_schema_text()

    log.info("translating question: %s", req.question[:120])
    nl_response = nl_to_sql.translate(req.question, schema_text)

    if not nl_response.sql:
        return JSONResponse(
            {
                "ok": False,
                "stage": "translate",
                "explanation": nl_response.explanation,
                "sql": "",
            }
        )

    log.info("validating SQL: %s", nl_response.sql.replace("\n", " ")[:200])
    safe = safety.validate_and_safe_sql(nl_response.sql)
    if not safe.ok:
        return JSONResponse(
            {
                "ok": False,
                "stage": "safety",
                "reason": safe.reason,
                "sql": nl_response.sql,
                "explanation": nl_response.explanation,
            }
        )

    log.info("running SQL")
    try:
        result = _state["db"].run_query(safe.sql)
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "stage": "execute",
                "reason": str(e),
                "sql": safe.sql,
                "explanation": nl_response.explanation,
            }
        )

    return {
        "ok": True,
        "sql": safe.sql,
        "explanation": nl_response.explanation,
        "columns": result.columns,
        "rows": result.rows[:500],  # cap response payload separately from query LIMIT
        "n_rows": len(result.rows),
        "columns_used": nl_response.columns_used,
    }

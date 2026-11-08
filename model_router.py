import asyncio
import json
import logging
import os
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("model-router")

app = FastAPI(title="Tenant-Aware Model Router")

DB_PATH = os.environ.get("ROUTER_DB_PATH", "token_usage.db")
TOKEN_LIMIT = 50_000
WINDOW_SECONDS = 60
PRIMARY_TIMEOUT_SECONDS = 3.0

PRIMARY_URL = os.environ.get("PRIMARY_MODEL_URL", "")
SECONDARY_URL = os.environ.get("SECONDARY_MODEL_URL", "")
PRIMARY_API_KEY = os.environ.get("PRIMARY_MODEL_API_KEY", "")
SECONDARY_API_KEY = os.environ.get("SECONDARY_MODEL_API_KEY", "")

# Approximate token estimate: one token per four characters.
# For production, replace this with the provider's tokenizer.
def estimate_tokens(payload: dict[str, Any]) -> int:
    text = json.dumps(payload, ensure_ascii=False)
    return max(1, (len(text) + 3) // 4)


@contextmanager
def db_connection():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db():
    with db_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_tenant_time
            ON token_usage (tenant_id, created_at)
        """)


def check_and_record_usage(tenant_id: str, tokens: int) -> tuple[bool, int]:
    """Atomically enforce the per-tenant rolling 60-second token limit."""
    now = time.time()
    cutoff = now - WINDOW_SECONDS

    with db_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM token_usage WHERE created_at <= ?",
            (cutoff,),
        )
        row = connection.execute(
            """
            SELECT COALESCE(SUM(tokens), 0)
            FROM token_usage
            WHERE tenant_id = ? AND created_at > ?
            """,
            (tenant_id, cutoff),
        ).fetchone()

        used = int(row[0])
        if tokens > TOKEN_LIMIT or used + tokens > TOKEN_LIMIT:
            return False, used

        connection.execute(
            """
            INSERT INTO token_usage (tenant_id, tokens, created_at)
            VALUES (?, ?, ?)
            """,
            (tenant_id, tokens, now),
        )
        return True, used + tokens


def sanitized_error(status_code: int, message: str):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "gateway_error"}},
    )


async def call_model(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code == 429:
        raise RuntimeError("provider_rate_limited")
    response.raise_for_status()
    return response.json()


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/usage")
def usage(authorization: str | None = Header(default=None)):
    # Use a tenant bearer token as the tenant identifier.
    # Replace with verified identity from your auth system in production.
    if not authorization or not authorization.startswith("Bearer "):
        return sanitized_error(401, "Authentication required")

    tenant_id = authorization.removeprefix("Bearer ").strip()
    if not tenant_id or len(tenant_id) > 200:
        return sanitized_error(401, "Invalid authentication")

    cutoff = time.time() - WINDOW_SECONDS
    with db_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(tokens), 0)
            FROM token_usage
            WHERE tenant_id = ? AND created_at > ?
            """,
            (tenant_id, cutoff),
        ).fetchone()

    return {
        "tenant_id": tenant_id,
        "tokens_used_last_minute": int(row[0]),
        "tokens_limit": TOKEN_LIMIT,
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not authorization or not authorization.startswith("Bearer "):
        return sanitized_error(401, "Authentication required")

    tenant_id = authorization.removeprefix("Bearer ").strip()
    if not tenant_id or len(tenant_id) > 200:
        return sanitized_error(401, "Invalid authentication")

    try:
        payload = await request.json()
    except Exception:
        return sanitized_error(400, "Invalid JSON request")

    if not isinstance(payload, dict):
        return sanitized_error(400, "Request must be a JSON object")

    tokens = estimate_tokens(payload)
    allowed, used = await asyncio.to_thread(
        check_and_record_usage, tenant_id, tokens
    )

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": "Tenant token limit exceeded",
                    "type": "rate_limit_error",
                    "tokens_used_last_minute": used,
                    "tokens_limit": TOKEN_LIMIT,
                }
            },
        )

    if not PRIMARY_URL or not SECONDARY_URL:
        return sanitized_error(
            503,
            "Model providers are not configured",
        )

    try:
        result = await call_model(
            PRIMARY_URL, PRIMARY_API_KEY, payload,
            PRIMARY_TIMEOUT_SECONDS,
        )
        return result
    except (httpx.TimeoutException, RuntimeError) as exc:
        # Only rate-limit responses and timeouts trigger fallback.
        logger.warning("Primary model unavailable; attempting fallback")
    except httpx.HTTPStatusError:
        logger.error("Primary model returned an unsuccessful response")
        return sanitized_error(502, "Model provider request failed")
    except (httpx.RequestError, ValueError):
        logger.error("Primary model request failed")
        return sanitized_error(502, "Model provider request failed")

    try:
        result = await call_model(
            SECONDARY_URL, SECONDARY_API_KEY, payload, 30.0
        )
        return result
    except Exception:
        logger.error("Secondary model request failed")
        return sanitized_error(502, "Model provider request failed")
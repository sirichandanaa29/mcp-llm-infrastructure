# QuilrAI Technical Assessment

Python implementation of four backend AI infrastructure tasks.

## Requirements

- Python 3.10+
- FastAPI
- Uvicorn
- httpx
- Official Python MCP SDK

## Setup

Create and activate a virtual environment:

python3 -m venv .venv
source .venv/bin/activate

Install dependencies:

pip install fastapi uvicorn httpx mcp

## Task 1: MCP Server

File: server.py

Implements customer-record lookup and refund tools using the MCP protocol.

Run:

python3 server.py

Uses stdio transport.

## Task 2: MCP Gateway

Files: gateway.py, downstream_mock.py

Implements an HTTP JSON-RPC gateway with role-based authorization.

Admin-only tools are restricted to admin callers.

## Task 3: Streaming LLM Guardrail

File: llm_gateway.py

Streams LLM output while redacting email addresses, Social Security numbers, and credit card numbers.

Run:

python3 -m uvicorn llm_gateway:app --port 8003

Without provider credentials, the gateway uses a mock streaming provider.

## Task 4: Tenant-Aware Model Router

Files: model_router.py, mock_model.py

Implements:
- Per-tenant rolling 60-second token quota
- 50,000-token-per-minute limit
- SQLite usage tracking
- Primary-to-secondary fallback on HTTP 429 or timeout
- Sanitized gateway errors

Start the mock provider:

python3 -m uvicorn mock_model:app --port 8005

In another terminal, configure and start the router:

export PRIMARY_MODEL_URL='http://127.0.0.1:8005/primary'
export SECONDARY_MODEL_URL='http://127.0.0.1:8005/secondary'
python3 -m uvicorn model_router:app --port 8004

## Validation

Local tests covered:
- MCP tool execution and gateway authorization
- Streaming PII redaction across chunk boundaries
- SQLite token tracking and quota rejection
- Primary HTTP 429 fallback
- Primary timeout fallback

## Implementation Notes

Token usage is estimated using character length rather than a model-specific tokenizer. The router uses demonstration bearer-token tenant identification; production deployment requires verified authentication. Real LLM provider integration requires provider credentials and has not been tested against a live provider.

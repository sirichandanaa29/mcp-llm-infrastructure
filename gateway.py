
import logging
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-gateway")

app = FastAPI(title="MCP Security Gateway")

DOWNSTREAM_URL = "http://127.0.0.1:8001/mcp"

TOKEN_ROLES = {
    "admin-token": "admin",
    "viewer-token": "viewer",
}


def rpc_error(request_id, code: int, message: str):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


@app.post("/mcp")
async def proxy(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=rpc_error(None, -32700, "Parse error"),
        )

    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        request_id = payload.get("id") if isinstance(payload, dict) else None
        return JSONResponse(
            status_code=400,
            content=rpc_error(request_id, -32600, "Invalid Request"),
        )

    request_id = payload.get("id")

    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    role = TOKEN_ROLES.get(token) if scheme.lower() == "bearer" else None

    if role is None:
        return JSONResponse(
            status_code=401,
            content=rpc_error(request_id, -32001, "Unauthorized"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    method = payload.get("method")

    if method == "tools/call":
        params = payload.get("params", {})
        tool_name = params.get("name", "") if isinstance(params, dict) else ""

        if not isinstance(tool_name, str):
            return JSONResponse(
                status_code=400,
                content=rpc_error(request_id, -32602, "Invalid tool parameters"),
            )

        if tool_name.startswith("admin_") and role != "admin":
            logger.warning("Blocked admin tool call for role=%s", role)
            return JSONResponse(
                status_code=200,
                content=rpc_error(request_id, -32001, "Unauthorized Tool Call"),
            )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.post(
                DOWNSTREAM_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        return JSONResponse(
            status_code=upstream.status_code,
            content=upstream.json(),
        )

    except (httpx.HTTPError, ValueError):
        logger.exception("Downstream request failed")
        return JSONResponse(
            status_code=502,
            content=rpc_error(request_id, -32603, "Gateway upstream error"),
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

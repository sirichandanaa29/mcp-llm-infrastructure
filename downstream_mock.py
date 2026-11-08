
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mock-downstream")

app = FastAPI(title="Mock Downstream MCP Server")


@app.post("/mcp")
async def handle_mcp(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
        )

    request_id = payload.get("id")

    if payload.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    method = payload.get("method")

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "get_status",
                        "description": "Read system status",
                    },
                    {
                        "name": "admin_reset_key",
                        "description": "Mock admin action",
                    },
                ]
            },
        }

    if method == "tools/call":
        params = payload.get("params", {})
        name = params.get("name", "unknown")

        logger.info("Mock downstream executed tool: %s", name)

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Mock downstream executed tool: {name}",
                    }
                ],
                "isError": False,
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)

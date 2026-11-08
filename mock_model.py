import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Model Provider")


@app.post("/primary")
async def primary(request: Request):
    mode = os.environ.get("PRIMARY_MOCK_MODE", "429")

    if mode == "429":
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Rate limited"}},
        )

    if mode == "timeout":
        await asyncio.sleep(10)

    payload = await request.json()
    return {
        "id": "mock-primary",
        "model": payload.get("model", "mock-model"),
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Response from primary mock model",
                },
                "finish_reason": "stop",
                "index": 0,
            }
        ],
    }


@app.post("/secondary")
async def secondary(request: Request):
    payload = await request.json()
    return {
        "id": "mock-secondary",
        "model": payload.get("model", "mock-model"),
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Response from secondary mock model",
                },
                "finish_reason": "stop",
                "index": 0,
            }
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok"}
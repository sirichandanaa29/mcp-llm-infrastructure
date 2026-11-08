
import asyncio
import json
import logging
import os
import re
import sys
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("llm-gateway")

app = FastAPI(title="Streaming LLM Guardrail Gateway")

REDACTED = "[REDACTED]"

EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9._%+-])",
    re.IGNORECASE,
)
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

PII_PATTERNS = [EMAIL_RE, SSN_RE, CARD_RE]

# An unfinished email or card number must remain buffered.
# This is a safety limit, not a maximum response length.
MAX_PENDING_CHARS = 320


def redact_pii(text: str) -> str:
    for pattern in PII_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def safe_release_length(text: str) -> int:
    """
    Return a prefix boundary that does not split a possible PII value.
    Keep unfinished words and trailing digit/separator sequences buffered.
    """
    if not text:
        return 0

    # Keep the trailing token, because an email may span provider chunks.
    boundary = max(
        text.rfind(" "),
        text.rfind("\n"),
        text.rfind("\t"),
        text.rfind("\r"),
    ) + 1

    # A card number may contain spaces, so retain a trailing digit sequence.
    candidate = re.search(r"\d(?:[\d -]*\d)?[ -]*$", text)
    if candidate:
        boundary = min(boundary, candidate.start())

    # Never split a complete sensitive value across the release boundary.
    for pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            if match.start() < boundary < match.end():
                boundary = match.start()

    # Bound memory use if an unusually long token never ends.
    # Fail closed rather than releasing a possibly sensitive unfinished token.
    if len(text) > MAX_PENDING_CHARS and boundary == 0:
        logger.warning("Long unfinished token retained for safety")

    return max(0, boundary)


def make_sse(text: str) -> bytes:
    event = {
        "choices": [
            {"index": 0, "delta": {"content": text}}
        ]
    }
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")


async def mock_provider_stream() -> AsyncIterator[str]:
    """Deterministic test provider; no API key required."""
    chunks = [
        "Hello! This response is streaming. ",
        "Contact me at alex.",
        "morgan@example.com for help. ",
        "The SSN is 123-",
        "45-6789 and card is ",
        "4111 1111 ",
        "1111 1111.",
        " Your request is complete. ",
        "Here is additional text to demonstrate incremental delivery. ",
        "The end of the test response.",
    ]

    for chunk in chunks:
        await asyncio.sleep(0.2)
        yield chunk


async def openai_compatible_stream(
    prompt: str,
) -> AsyncIterator[str]:
    """Read text deltas from an OpenAI-compatible streaming endpoint."""
    url = os.environ["LLM_API_URL"]
    api_key = os.environ["LLM_API_KEY"]
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", url, headers=headers, json=payload
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()
                if data == "[DONE]":
                    break

                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Ignoring malformed provider SSE event")
                    continue

                for choice in event.get("choices", []):
                    content = choice.get("delta", {}).get("content")
                    if isinstance(content, str) and content:
                        yield content


async def provider_stream(prompt: str) -> AsyncIterator[str]:
    # Configure all three environment variables to use a real provider.
    # Without them, use the local mock for repeatable testing.
    if (
        os.environ.get("LLM_API_URL")
        and os.environ.get("LLM_API_KEY")
    ):
        async for text in openai_compatible_stream(prompt):
            yield text
    else:
        async for text in mock_provider_stream():
            yield text

async def guarded_stream(
    source: AsyncIterator[str],
) -> AsyncIterator[bytes]:
    text_buffer = ""

    try:
        async for content in source:
            text_buffer += content
            if len(text_buffer) > MAX_PENDING_CHARS:
                raise ValueError("PII buffer limit exceeded")
            boundary = safe_release_length(text_buffer)
            if boundary > 0:
                safe_text = text_buffer[:boundary]
                text_buffer = text_buffer[boundary:]
                yield make_sse(redact_pii(safe_text))

    except Exception:
        # Do not expose upstream details or flush unverified buffered text.
        logger.error("LLM provider stream failed")
        error_event = {
            "error": {
                "message": "LLM provider request failed",
                "type": "gateway_error",
            }
        }
        yield f"data: {json.dumps(error_event)}\n\n".encode("utf-8")
        return

    if text_buffer:
        yield make_sse(redact_pii(text_buffer))

    yield b"data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON request"}},
        )

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "prompt must be a string"}},
        )

    logger.info("Starting guarded response stream")

    async def safe_provider() -> AsyncIterator[str]:
        try:
            async for chunk in provider_stream(prompt):
                yield chunk
        except Exception:
            # Do not log API keys, prompts, or raw upstream error bodies.
            logger.error("LLM provider stream failed")
            raise

    return StreamingResponse(
        guarded_stream(safe_provider()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8003)
 

"""Proxy /v1/chat/completions to the configured LLM backend.

This allows Hermes Workspace to detect chat capabilities during onboarding
while keeping the actual LLM inference on the local MLX server.
"""

import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hermes_cli.config import load_config

router = APIRouter()


def _get_llm_base_url() -> str:
    """Resolve the LLM backend URL from config or env."""
    config = load_config()
    model_config = config.get("model", {})
    if isinstance(model_config, dict):
        base_url = model_config.get("base_url", "")
        if base_url:
            return base_url.rstrip("/")
    return os.getenv("OPENAI_BASE_URL", "http://localhost:8080/v1").rstrip("/")


@router.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    """Forward chat completions to the local LLM backend."""
    base_url = _get_llm_base_url()
    # Ensure we hit /v1/chat/completions
    if not base_url.endswith("/v1"):
        target = f"{base_url}/v1/chat/completions"
    else:
        target = f"{base_url}/chat/completions"

    body = await request.body()
    headers = {
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(target, content=body, headers=headers)
            return JSONResponse(
                content=resp.json(),
                status_code=resp.status_code,
            )
        except httpx.ConnectError:
            return JSONResponse(
                content={"error": {"message": "LLM backend not reachable", "type": "connection_error"}},
                status_code=502,
            )
        except Exception as e:
            return JSONResponse(
                content={"error": {"message": str(e), "type": "proxy_error"}},
                status_code=500,
            )

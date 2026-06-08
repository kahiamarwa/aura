import logging
from collections.abc import AsyncGenerator

import httpx

from app.schemas import TranscriptionSegment

logger = logging.getLogger(__name__)


def _build_context_string(context: list[TranscriptionSegment]) -> str:
    if not context:
        return ""
    lines = []
    for seg in context:
        lines.append(seg.text)
    return "\n".join(lines)


def _build_agent_headers(agent_token: str, user_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
        headers["apikey"] = agent_token
    else:
        headers["Authorization"] = f"Bearer {agent_token}"
    return headers


async def get_response(
    command: str,
    context: list[TranscriptionSegment],
    agent_url: str,
    agent_token: str,
    user_token: str | None = None,
    enriched_context: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    context_string = enriched_context if enriched_context else _build_context_string(context)

    logger.info("Calling agent: command=%s, context_len=%d, has_user_token=%s, conversation_id=%s",
                command[:80], len(context_string), bool(user_token), conversation_id)

    headers = _build_agent_headers(agent_token, user_token)

    body: dict[str, str | None] = {
        "message": command,
        "context": context_string,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            agent_url,
            headers=headers,
            json=body,
        )
        logger.info("Agent HTTP status: %d", response.status_code)
        if response.status_code >= 400:
            logger.error("Agent error body: %s", response.text[:500])
        response.raise_for_status()
        data = response.json()
        logger.info("Agent response keys: %s", list(data.keys()))
        return {"text": data["response"], "attachments": data.get("attachments")}


async def stream_response(
    command: str,
    context: list[TranscriptionSegment],
    agent_url: str,
    agent_token: str,
    user_token: str | None = None,
    enriched_context: str | None = None,
    conversation_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Proxy streaming SSE from aura-agent edge function."""
    context_string = enriched_context if enriched_context else _build_context_string(context)

    logger.info("Calling agent (streaming): command=%s, context_len=%d",
                command[:80], len(context_string))

    headers = _build_agent_headers(agent_token, user_token)

    body: dict = {
        "message": command,
        "context": context_string,
        "stream": True,
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", agent_url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                error_body = ""
                async for chunk in response.aiter_text():
                    error_body += chunk
                logger.error("Agent stream error: %d %s", response.status_code, error_body[:500])
                yield f"event: error\ndata: {{}}\n\n"
                return

            async for line in response.aiter_lines():
                if line.strip():
                    yield line + "\n"
                else:
                    yield "\n"

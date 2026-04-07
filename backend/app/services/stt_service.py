import asyncio

import httpx


async def create_stt_token(api_key: str, retries: int = 2) -> str:
    last_err = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe",
                    headers={"xi-api-key": api_key},
                    json={},
                )
                response.raise_for_status()
                return response.json()["token"]
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                await asyncio.sleep(1)
    raise last_err  # type: ignore[misc]

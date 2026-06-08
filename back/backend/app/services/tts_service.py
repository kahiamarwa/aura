from collections.abc import AsyncGenerator

import httpx


async def stream_tts(
    text: str,
    voice_id: str,
    api_key: str,
) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_flash_v2_5",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
                "output_format": "mp3_22050_32",
            },
            timeout=30.0,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=4096):
                yield chunk

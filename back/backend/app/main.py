import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Force UTF-8 on all log streams (Docker pipes may default to ASCII) ──
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(stream=open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False))],
)

from app.routes import activity, chat, contacts, conversations, device_converse, device_enroll, device_pairing, device_stream, discussions, gemini_stt, health, intent_classifier, settings, speakers, stt_token, summaries, wakeword

app = FastAPI(title="AURA POC Backend")

_default_origins = "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003"
allowed_origins = os.getenv("CORS_ORIGINS", _default_origins).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(health.router)
app.include_router(stt_token.router)
app.include_router(chat.router)
app.include_router(wakeword.router)
app.include_router(summaries.router)
app.include_router(contacts.router)
app.include_router(activity.router)
app.include_router(discussions.router)
app.include_router(settings.router)
app.include_router(conversations.router)
app.include_router(gemini_stt.router)
app.include_router(speakers.router)
app.include_router(intent_classifier.router)
app.include_router(device_converse.router)
app.include_router(device_pairing.router)
app.include_router(device_stream.router)   # chemin B : proxy WS streaming STT
app.include_router(device_enroll.router)   # enrôlement vocal depuis l'enceinte


@app.on_event("startup")
async def _preload_speaker_model():
    """Eagerly load ONNX speaker model so the first request isn't slow."""
    from app.services.speaker_service import SpeakerService
    SpeakerService.get_instance()  # ONNX loads in ~100ms

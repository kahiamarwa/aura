import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import activity, chat, contacts, conversations, discussions, gemini_stt, health, settings, speakers, stt_token, summaries, wakeword

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

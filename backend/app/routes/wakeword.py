import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import numpy as np
from app.services.wakeword_service import WakeWordService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
router = APIRouter()


@router.websocket("/api/wakeword")
async def wakeword_ws(websocket: WebSocket):
    await websocket.accept()
    print("[WakeWord] WebSocket CONNECTED")
    service = WakeWordService.get_instance()
    print(f"[WakeWord] Model loaded, names={service.model_names}, threshold={service.threshold}")

    frame_count = 0
    try:
        while True:
            data = await websocket.receive_bytes()
            audio_array = np.frombuffer(data, dtype=np.int16)
            frame_count += 1

            # Log toutes les 10 frames (~1s) pour debug
            if frame_count % 10 == 1:
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                print(f"[WakeWord] frame={frame_count} samples={len(audio_array)} rms={rms:.1f}")

            result = service.process_audio(audio_array)

            # Afficher tous les scores pour debug (sans re-predict)
            if frame_count % 10 == 1:
                # Lire les scores du dernier predict déjà fait dans process_audio
                scores = {name: service.model.prediction_buffer[name][-1] for name in service.model_names}
                print(f"[WakeWord] scores={scores}")

            if result is not None:
                print(f"[WakeWord] >>> DETECTED! model={result['model']} score={result['score']:.4f}")
                await websocket.send_json({
                    "event": "wake_word_detected",
                    "model": result["model"],
                    "score": result["score"],
                })
    except WebSocketDisconnect:
        print("[WakeWord] WebSocket DISCONNECTED")

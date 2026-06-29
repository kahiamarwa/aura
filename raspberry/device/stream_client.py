"""Client WebSocket du device → backend (chemin B : streaming STT + turn-taking).

Le Pi streame le PCM 16k au backend, qui relaie vers DEEPGRAM FLUX (STT + détection de
fin de tour CÔTÉ SERVEUR). Le device reçoit les transcripts partiels au fil de l'eau, le
signal 'turn_end' (Flux a décidé la fin du tour), puis la réponse texte + le MP3 TTS
streamé. Synchrone (websocket-client) + thread receveur → file. AUCUNE clé sur l'appareil :
auth par DEVICE_TOKEN.

Messages reçus (kind) : "partial" (transcript partiel), "turn_end" (fin de tour Flux),
"response" (texte LLM), "audio" (frames MP3 binaires), "audio_end", "final", "error", "closed".
"""
import json
import queue
import logging
import threading

logger = logging.getLogger(__name__)


def ws_url_from_http(base_url: str) -> str:
    """https://host → wss://host/api/device-stream (ws:// pour http://)."""
    u = base_url.rstrip("/")
    if u.startswith("https://"):
        u = "wss://" + u[len("https://"):]
    elif u.startswith("http://"):
        u = "ws://" + u[len("http://"):]
    return u + "/api/device-stream"


class StreamClient:
    """Connexion WS au backend. Streame le PCM, reçoit transcripts + réponse + MP3."""

    def __init__(self, ws_url: str, device_token: str):
        self._url = ws_url
        self._token = device_token
        self._ws = None
        self._rx: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._alive = False

    def connect(self, timeout: float = 12.0) -> bool:
        try:
            import websocket  # websocket-client
        except ImportError:
            logger.error("[stream] websocket-client manquant → pip install websocket-client")
            return False
        try:
            self._ws = websocket.create_connection(
                self._url, header=[f"X-Device-Token: {self._token}"], timeout=timeout)
            self._ws.settimeout(None)
            self._alive = True
            threading.Thread(target=self._receiver, daemon=True).start()
            return True
        except Exception as e:
            logger.warning("[stream] connexion WS échouée (%s): %s", self._url, e)
            return False

    def _receiver(self):
        while self._alive:
            try:
                msg = self._ws.recv()
            except Exception:
                break
            if not msg:
                continue
            if isinstance(msg, (bytes, bytearray)):
                self._rx.put(("audio", bytes(msg)))      # frame MP3 du TTS
            else:
                try:
                    data = json.loads(msg)
                    self._rx.put((data.get("type", "?"), data))
                except Exception:
                    pass
        self._rx.put(("closed", {}))

    # ── envois ──
    # NB : pas de send_eot — c'est DEEPGRAM FLUX (côté serveur) qui décide la fin de tour,
    # le device ne signale jamais l'EOT lui-même.
    def send_pcm(self, pcm: bytes):
        try:
            self._ws.send_binary(pcm)
        except Exception:
            self._on_send_error()

    def send_cancel(self):
        self._send_json({"type": "cancel"})

    def send_speaker(self, verified: bool, name: str, score: float):
        """Résultat de la VÉRIF LOCUTEUR faite EN LOCAL (ECAPA sur le Pi) → le backend gate."""
        self._send_json({"type": "speaker", "verified": bool(verified),
                         "name": name or "", "score": round(float(score), 4)})

    def _send_json(self, obj):
        try:
            self._ws.send(json.dumps(obj))
        except Exception:
            self._on_send_error()

    def _on_send_error(self):
        """TCP rompu (souvent half-open, settimeout(None)) : on réveille le consommateur
        avec ('closed') au lieu de continuer à streamer dans le vide (dead-air silencieux)."""
        if self._alive:
            self._alive = False
            self._rx.put(("closed", {}))

    # ── réception ──
    def recv(self, timeout: float):
        """(kind, data) ou None si timeout."""
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self._alive = False
        try:
            self._ws.close()
        except Exception:
            pass

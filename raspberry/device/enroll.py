"""Enrôlement vocal GUIDÉ depuis l'enceinte (le bon micro → score fiable).

Déclenché par le WEB (enroll_request lu via /api/device/control). Guide la capture avec
SON micro : prompts vocaux (servis par le backend, TTS + cache) + LED + bips, capture N
segments, puis envoie l'audio au backend (ECAPA côté serveur — aucune clé sur l'appareil).
Pousse l'avancement (state ENROLLING + « i/N ») → affiché en direct sur le web.

IMPORTANT (AEC) : les prompts sont joués dans un THREAD pendant qu'on DRAINE le micro en
parallèle. Sinon, avec l'echo-cancel PipeWire, le sink de lecture se couple à la source :
micro non lu → mpg123 fige (deadlock). Le drain + un timeout garantissent zéro blocage.
"""
import logging
import threading
import time
from pathlib import Path

import numpy as np

from . import config, cloud
from .audio_io import play_beep

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
N_SEGMENTS = 4
SEGMENT_S = 5.0
PROMPT_MAX_S = 12.0          # garde-fou : un prompt ne bloque jamais plus longtemps


def _prompt_bytes(name: str) -> bytes | None:
    """MP3 du prompt : cache local d'abord, sinon récupéré du backend (TTS + cache) et
    mis en cache local. None si tout échoue (→ l'appelant fera un bip)."""
    path = PROMPTS_DIR / f"{name}.mp3"
    if path.exists():
        try:
            return path.read_bytes()
        except Exception:
            pass
    data = cloud.get_enroll_prompt(name)
    if data:
        try:
            PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception:
            pass
    return data


def _play_and_drain(orch, frames, name: str):
    """Joue le prompt dans un THREAD et DRAINE le micro en parallèle (anti-deadlock AEC),
    borné à PROMPT_MAX_S. Les frames lues ici sont jetées (on ne capture pas le prompt)."""
    data = _prompt_bytes(name)

    def _play():
        if data:
            try:
                orch.player.play_mp3(data)
                return
            except Exception as e:
                logger.debug("[enroll] play %s KO: %s", name, e)
        play_beep()

    t = threading.Thread(target=_play, daemon=True)
    t.start()
    deadline = time.monotonic() + PROMPT_MAX_S
    while t.is_alive() and time.monotonic() < deadline:
        try:
            next(frames)                 # draine + jette → garde le sink AEC débloqué
        except StopIteration:
            break
    if t.is_alive():
        logger.warning("[enroll] prompt %s trop long → on coupe", name)
        orch.player.stop()
        t.join(timeout=1.0)


def _capture_segment(frames, n_samples: int):
    """Lit ~n_samples du micro (frames int16) → PCM concaténé (ou None)."""
    got, chunks = 0, []
    while got < n_samples:
        try:
            fr = next(frames)
        except StopIteration:
            break
        chunks.append(fr)
        got += len(fr)
    return np.concatenate(chunks) if chunks else None


def run(orch, frames, req: dict) -> None:
    """Flux complet d'enrôlement guidé (BLOQUANT). orch = Orchestrator (mic, led, player)."""
    name = (req or {}).get("name") or "moi"
    led = orch.led
    logger.info("[enroll] début enrôlement de « %s »", name)
    orch.ambient.set_enabled(False)
    orch._push_enroll(f"Apprentissage de la voix de {name}")
    led.enroll("wait")
    _play_and_drain(orch, frames, "enroll_intro")

    seg_len = int(SEGMENT_S * config.SAMPLE_RATE)
    samples = []
    for i in range(N_SEGMENTS):
        led.enroll("pause")
        prompt = ("enroll_speak" if i == 0
                  else "enroll_almost" if i == N_SEGMENTS - 1
                  else "enroll_continue")
        _play_and_drain(orch, frames, prompt)
        if getattr(orch, "mic", None):
            orch.mic.flush()              # repart sur de l'audio FRAIS pour la capture
        play_beep()                       # bip = vas-y (non bloquant)
        led.enroll("speak")
        orch._push_enroll(f"{i + 1}/{N_SEGMENTS}")
        seg = _capture_segment(frames, seg_len)
        if seg is not None:
            samples.append(seg)
        led.enroll("pause")

    led.enroll("process")
    orch._push_enroll("Traitement…")
    if not samples:
        led.enroll("fail")
        _play_and_drain(orch, frames, "enroll_fail")
        logger.info("[enroll] aucun audio capté")
        return
    wav = cloud.pcm_to_wav_bytes(np.concatenate(samples))
    res = cloud.enroll(name, wav)
    if res.get("ok"):
        led.enroll("done")
        _play_and_drain(orch, frames, "enroll_done")
        logger.info("[enroll] « %s » enrôlé ✓ (%ss réf)", name, res.get("reference_duration_s"))
    else:
        led.enroll("fail")
        _play_and_drain(orch, frames, "enroll_fail")
        logger.info("[enroll] échec : %s", res.get("status"))
    if getattr(orch, "mic", None):
        orch.mic.flush()

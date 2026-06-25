"""Enrôlement vocal GUIDÉ depuis l'enceinte (le bon micro → score fiable).

Déclenché par le WEB (enroll_request lu via /api/device/control). Guide la capture avec
SON micro : prompts vocaux MP3 embarqués (device/prompts/) + LED + bips, capture N segments,
puis envoie l'audio au backend (ECAPA côté serveur — aucune clé sur l'appareil). Pousse
l'avancement (state ENROLLING + « i/N ») → affiché en direct sur le web.
"""
import logging
from pathlib import Path

import numpy as np

from . import config, cloud
from .audio_io import play_beep

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
N_SEGMENTS = 4
SEGMENT_S = 5.0


def _play_prompt(orch, name: str):
    """Joue un prompt vocal MP3 (bloquant). D'abord le cache local, sinon on le récupère
    du BACKEND (TTS + cache serveur) et on le met en cache local. Fallback bip si tout échoue."""
    path = PROMPTS_DIR / f"{name}.mp3"
    data = None
    if path.exists():
        try:
            data = path.read_bytes()
        except Exception:
            data = None
    if data is None:
        data = cloud.get_enroll_prompt(name)        # récupère depuis le backend
        if data:
            try:
                PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)              # cache local pour la prochaine fois
            except Exception:
                pass
    if data:
        try:
            orch.player.play_mp3(data)
            return
        except Exception as e:
            logger.debug("[enroll] prompt %s KO: %s", name, e)
    play_beep()


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
    _play_prompt(orch, "enroll_intro")

    seg_len = int(SEGMENT_S * config.SAMPLE_RATE)
    samples = []
    for i in range(N_SEGMENTS):
        led.enroll("pause")
        prompt = ("enroll_speak" if i == 0
                  else "enroll_almost" if i == N_SEGMENTS - 1
                  else "enroll_continue")
        _play_prompt(orch, prompt)
        if getattr(orch, "mic", None):
            orch.mic.flush()              # ne PAS capter le prompt lui-même
        play_beep()                       # bip = vas-y
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
        _play_prompt(orch, "enroll_fail")
        logger.info("[enroll] aucun audio capté")
        return
    wav = cloud.pcm_to_wav_bytes(np.concatenate(samples))
    res = cloud.enroll(name, wav)
    if res.get("ok"):
        led.enroll("done")
        _play_prompt(orch, "enroll_done")
        logger.info("[enroll] « %s » enrôlé ✓ (%ss réf)", name, res.get("reference_duration_s"))
    else:
        led.enroll("fail")
        _play_prompt(orch, "enroll_fail")
        logger.info("[enroll] échec : %s", res.get("status"))

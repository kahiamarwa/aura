"""Contrôleur de la LED RGB (KY-016) : reflète l'état d'Aura sur une LED physique.

Mêmes repères que l'orbe du front : écoute=vert, réflexion=bleu (respiration),
parle=violet, suivi=cyan, muet/micro coupé=rouge, repos=ambre doux.

Optionnel (LED_ENABLED). Dégradation gracieuse : si gpiozero ou le matériel
manque, le contrôleur devient un no-op — Aura tourne normalement, sans LED.
Le vert paraissant plus lumineux à l'œil, l'ambre est calibré (peu de vert).
"""
import logging

from . import config

logger = logging.getLogger(__name__)

# Couleur (R, G, B) ∈ [0,1] par état.
_COLORS = {
    "IDLE":       (0.45, 0.10, 0.0),   # ambre doux (repos) — vert bas = vrai orange
    "LISTENING":  (0.0, 1.0, 0.1),     # vert
    "SPEAKING":   (0.6, 0.2, 1.0),     # violet
    "CONVERSING": (0.0, 0.8, 1.0),     # cyan
    "MUTED":      (1.0, 0.0, 0.0),     # rouge (mic coupé / perdu)
}
_OFF = (0.0, 0.0, 0.0)


class LedController:
    """set_state(state) → couleur. THINKING respire en bleu. Jamais ne lève."""

    def __init__(self):
        self._led = None
        if not config.LED_ENABLED:
            return
        try:
            from gpiozero import RGBLED
            # active_high=True : KY-016 = cathode commune (broche GND).
            self._led = RGBLED(red=config.LED_R_PIN, green=config.LED_G_PIN,
                               blue=config.LED_B_PIN, active_high=True)
            logger.info("[LED] activée (R=GPIO%d G=GPIO%d B=GPIO%d)",
                        config.LED_R_PIN, config.LED_G_PIN, config.LED_B_PIN)
        except Exception as e:
            logger.warning("[LED] indisponible (%s) → pas de LED (Aura tourne quand même)", e)
            self._led = None

    def set_state(self, state: str):
        if not self._led:
            return
        try:
            if state == "THINKING":
                # respiration bleue = « je réfléchis » (effet vivant comme l'orbe)
                self._led.pulse(fade_in_time=0.7, fade_out_time=0.7,
                                on_color=(0.0, 0.2, 1.0), off_color=(0.0, 0.0, 0.08))
                return
            self._led.color = _COLORS.get(state, _OFF)   # met fin à un pulse en cours
        except Exception as e:
            logger.debug("[LED] set_state(%s) error: %s", state, e)

    def enroll(self, phase: str):
        """Patterns LED dédiés à l'enrôlement vocal (guidage visuel) :
        wait=blanc (prépare-toi), speak=vert clignotant (PARLE), pause=ambre,
        process=bleu pulsé, done=vert ×3, fail=rouge ×3."""
        if not self._led:
            return
        try:
            if phase == "wait":
                self._led.color = (0.6, 0.6, 0.6)
            elif phase == "speak":
                self._led.blink(on_time=0.4, off_time=0.25,
                                on_color=(0.0, 1.0, 0.1), off_color=(0.0, 0.05, 0.0),
                                background=True)
            elif phase == "pause":
                self._led.color = (0.45, 0.10, 0.0)
            elif phase == "process":
                self._led.pulse(fade_in_time=0.5, fade_out_time=0.5,
                                on_color=(0.0, 0.2, 1.0), off_color=(0.0, 0.0, 0.08))
            elif phase == "done":
                self._led.blink(on_time=0.15, off_time=0.15,
                                on_color=(0.0, 1.0, 0.1), off_color=_OFF, n=3, background=True)
            elif phase == "fail":
                self._led.blink(on_time=0.2, off_time=0.2,
                                on_color=(1.0, 0.0, 0.0), off_color=_OFF, n=3, background=True)
        except Exception as e:
            logger.debug("[LED] enroll(%s) error: %s", phase, e)

    def off(self):
        if self._led:
            try:
                self._led.off()
            except Exception:
                pass

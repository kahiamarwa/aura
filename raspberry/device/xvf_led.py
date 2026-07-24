"""Anneau LED WS2812 (12 LED) du ReSpeaker XVF3800 = indicateur d'état d'Aura.

Les utilisateurs du produit sont malvoyants : l'anneau de l'array (bien plus
visible que la petite LED GPIO KY-016) devient l'indicateur d'état principal.
Ce module tourne EN PARALLÈLE du LedController GPIO existant, derrière le flag
`XVF_LED=1` (OFF par défaut). Expérimental — firmware ReSpeaker ≥ 2.0.10 requis.

Protocole (reconstitué depuis `python_control/xvf_host.py`, cf. manuel §2.2/§2.8) :
  - ÉCRITURE vendor : `ctrl_transfer(bmRequestType=0x40, bRequest=0,
    wValue=cmdid, wIndex=resid, payload)`. Payload little-endian
    (uint8 = 1 o ; uint32 = 4 o). C'est EXACTEMENT le `ReSpeaker.write()` de
    xvf_host.py : un simple transfert OUT, SANS octet de statut ni relecture.
    Le statut `SERVICER_COMMAND_RETRY = 64` et la boucle de retry n'existent que
    sur le chemin de LECTURE (transferts IN) — qu'on ÉVITE volontairement ici
    (une lecture `DOA_VALUE` gèle si l'effet ≠ doa sur fw < 2.0.10, bug #16, et
    marteler la puce est la cause du bug #18).
  - On n'écrit QUE sur CHANGEMENT d'état (dédup) et via un thread worker
    (latest-wins) : la boucle audio n'est JAMAIS bloquée par un transfert USB.

⚠️ Garde-fous manuel §4 (bug #18) : pas de martèlement LED, JAMAIS de combo
   LED/GPO, et **JAMAIS `save_configuration`** (aucune persistance flash ici —
   assertion : ce module n'envoie AUCUNE commande resid 48). Fail-safe : après
   3 échecs consécutifs on coupe DÉFINITIVEMENT toute écriture (un device à
   moitié cassé ne doit plus être martelé).
"""
import os
import time
import queue
import struct
import atexit
import logging
import threading

logger = logging.getLogger(__name__)

# pyusb : import gardé — absent (ou pas de backend libusb) → module no-op total.
try:
    import usb.core
    import usb.util
    _BM_REQUEST_OUT = (usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR
                       | usb.util.CTRL_RECIPIENT_DEVICE)   # == 0x40
    _USB_OK = True
except Exception:                     # pragma: no cover - dépend du matériel
    usb = None
    _BM_REQUEST_OUT = 0x40
    _USB_OK = False

# Identité USB de l'array (interface de contrôle vendor).
_VID = 0x2886
_PID = 0x001A

_TIMEOUT_MS = 500          # transfert court : ne jamais bloquer longtemps
_MAX_FAILS = 3             # échecs consécutifs → désactivation définitive

# ── Commandes LED (resid 20 — cf. §2.8). (resid, cmdid, type, nb_valeurs) ──
_LED_EFFECT = (20, 12, "uint8")      # 0=off 1=breath 2=rainbow 3=solid 4=doa 5=ring
_LED_BRIGHTNESS = (20, 13, "uint8")  # 0-255
_LED_GAMMIFY = (20, 14, "uint8")     # correction gamma (teintes fidèles) — écrite à l'init
_LED_SPEED = (20, 15, "uint8")       # vitesse breath/rainbow (échelle définie par la puce)
_LED_COLOR = (20, 16, "uint32")      # couleur breath / solid
_LED_DOA_COLOR = (20, 17, "uint32")  # 2×uint32 : couleur de base + couleur du pointeur DoA
_LED_RING = (20, 19, "uint32")       # 12×uint32 : une couleur PAR LED (fw ≥ 2.0.7) —
                                     # validé terrain 24/07 via sonde USB brute (le CLI
                                     # Seeed ne connaît pas cette commande, le fw si)

# Ids d'effet
_OFF, _BREATH, _RAINBOW, _SOLID, _DOA, _RING = 0, 1, 2, 3, 4, 5

# Vitesses (échelle non documentée par XMOS). DÉCOUVERTE terrain 17/07 : la
# vitesse n'est LATCHÉE qu'au DÉMARRAGE de l'effet → il faut écrire la vitesse
# AVANT, puis redémarrer l'effet (off→on) — c'est ce que fait _apply. Valeurs
# validées à l'œil : 1 = respiration calme (veille), 2 = pulsé posé (réponse,
# terrain 24/07 : 6 était trop rapide). Réglables par env.
_SLOW = int(os.getenv("XVF_LED_SPEED_SLOW", "1"))
_MED = int(os.getenv("XVF_LED_SPEED_MED", "2"))
_FAST = int(os.getenv("XVF_LED_SPEED_FAST", "10"))

# Chenillard THINKING (idée + réglage utilisateur, terrain 24/07) : un pas
# toutes les 150 ms = ~1.8 s par tour, comète 1 LED pleine + 2 LED de traîne.
_CHASE_STEP_S = float(os.getenv("XVF_LED_CHASE_STEP_S", "0.15"))

# Luminosité : multiplicateur global réglable (XVF_LED_BRIGHTNESS_SCALE=1.5 =
# +50 % partout, plafonné 255). Terrain 16/07 : la veille à 60 était trop faible.
_SCALE = float(os.getenv("XVF_LED_BRIGHTNESS_SCALE", "1.0"))


def _b(v: int) -> int:
    """Luminosité d'état × échelle globale, bornée 5-255."""
    return min(255, max(5, int(v * _SCALE)))


def _rgb(c: int) -> int:
    """Convertit une constante lisible 0xRRGGBB en l'uint32 envoyé à la puce.

    D'après xvf_host.py + le manuel §2.8, le firmware ReSpeaker prend directement
    0xRRGGBB (`0xFF0000` = rouge) → ici IDENTITÉ. SI l'anneau affiche R et B
    inversés sur le vrai matériel (certains firmwares XIAO/I2C décodent
    R = c & 0xFF, soit 0xBBGGRR), basculer sur la variante commentée ci-dessous.
    UN SEUL endroit à corriger, device en main.
    """
    return c & 0xFFFFFF
    # Variante R/B inversés (à activer seulement si l'anneau montre les couleurs swappées) :
    # return ((c & 0xFF) << 16) | (c & 0xFF00) | ((c >> 16) & 0xFF)


# Palette VoiceNode (document de design Hallia 07/26, validée LED par LED à
# l'œil le 24/07). Langage : couleur = QUEL état ; mouvement = ce qu'Aura FAIT
# (respiration lente=veille, directionnel=attention, rotation=travail,
# pulsé=parole, rouge immobile=mute).
_BLEU = 0x3D8BFD       # VEILLE (respiration lente) + pointeur DoA d'écoute
_VERT = 0x2FD573       # ÉCOUTE (base) / RÉPONSE (respiration posée) / pointeur suivi
_ORANGE = 0xFFA02E     # RÉFLEXION — comète du chenillard
_TRAINE = 0x201404     # RÉFLEXION — traîne sombre de la comète (2 LED)
_ROUGE = 0xF0433A      # MUTED (fixe immobile = rien n'écoute) / ERROR (pouls bref)
_BLANC = 0xB0B0B0      # ENROLLING — cérémonie neutre
# THINKING = CHENILLARD logiciel (idée utilisateur 24/07) : mode ring (une
# couleur par LED) + rotation d'une comète orange pilotée par le worker
# (~6.7 écritures/s pendant la réflexion seulement — période courte et bornée,
# validé fluide à l'œil ; le rainbow natif est abandonné : couleurs figées fw).

# ── État Aura → séquence d'écritures ──
# RECETTE (terrain 16-17/07, réconcilie les deux observations) :
#   1. PARAMÈTRES d'abord (couleur/vitesse/luminosité) ;
#   2. puis REDÉMARRAGE de l'effet (off → effet cible) : la VITESSE n'est
#      latchée qu'au démarrage de l'effet (écrite après, elle est ignorée —
#      d'où la respiration d'usine « qui vibre » qu'on n'arrivait pas à calmer).
#      La couleur, elle, survit au redémarrage (validé à l'œil : off→on garde
#      l'ambre). Le off→on ajoute ~1 écriture par changement d'état — toujours
#      borné (changements d'état seulement, jamais de boucle).
_STATE_WRITES = {
    # Veille : respiration BLEUE douce et LENTE (vitesse 1), discrète (20-30 %).
    "IDLE": [
        (_LED_COLOR, [_rgb(_BLEU)]),
        (_LED_SPEED, [_SLOW]),
        (_LED_BRIGHTNESS, [_b(100)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_BREATH]),
    ],
    # Écoute : DoA bicolore (choix utilisateur 24/07, combo « C ») — anneau
    # VERT plein (« j'écoute ») + pointeur BLEU qui suit le locuteur (« toi »).
    "LISTENING": [
        (_LED_DOA_COLOR, [_rgb(_VERT), _rgb(_BLEU)]),
        (_LED_BRIGHTNESS, [_b(220)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_DOA]),
    ],
    # Réflexion : CHENILLARD orange (mode ring) — les frames sont écrites par le
    # worker (_chase_step) tant que l'état reste THINKING.
    "THINKING": [
        (_LED_BRIGHTNESS, [_b(160)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_RING]),
    ],
    # Réponse : respiration VERTE posée (vitesse 2 validée 24/07 — « pulsé »
    # du document, le firmware ne permettant pas la modulation par la voix).
    "SPEAKING": [
        (_LED_COLOR, [_rgb(_VERT)]),
        (_LED_SPEED, [_MED]),
        (_LED_BRIGHTNESS, [_b(150)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_BREATH]),
    ],
    # Suivi : miroir de l'écoute — anneau BLEU (repos actif) + pointeur VERT
    # (« je te suis encore, tu peux enchaîner »).
    "CONVERSING": [
        (_LED_DOA_COLOR, [_rgb(_BLEU), _rgb(_VERT)]),
        (_LED_BRIGHTNESS, [_b(140)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_DOA]),
    ],
    # Micro coupé : ROUGE FIXE immobile (contrat : rien ne bouge = rien n'écoute).
    "MUTED": [
        (_LED_COLOR, [_rgb(_ROUGE)]),
        (_LED_BRIGHTNESS, [_b(100)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_SOLID]),
    ],
    # Erreur : pouls ROUGE rapide et vif (flash bref via flash_error, puis retour).
    "ERROR": [
        (_LED_COLOR, [_rgb(_ROUGE)]),
        (_LED_SPEED, [_FAST]),
        (_LED_BRIGHTNESS, [_b(230)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_BREATH]),
    ],
    # Enrôlement vocal : respiration BLANCHE douce (même calme que la veille).
    "ENROLLING": [
        (_LED_COLOR, [_rgb(_BLANC)]),
        (_LED_SPEED, [_SLOW]),
        (_LED_BRIGHTNESS, [_b(140)]),
        (_LED_EFFECT, [_OFF]),
        (_LED_EFFECT, [_BREATH]),
    ],
}

# Sentinelle interne : flash d'erreur (appliqué ~1.5 s puis retour à l'état courant).
_ERROR_FLASH = "_ERROR_FLASH"

# Auto-test visuel au démarrage (XVF_LED_TEST=1) : cycle TOUS les états ~2 s
# chacun pour valider couleurs/effets d'un coup, sans piloter l'assistant.
_SELF_TEST = os.getenv("XVF_LED_TEST", "0") == "1"

_STOP = object()   # sentinelle d'arrêt du worker


class XvfLedRing:
    """Pilote l'anneau LED de l'XVF3800 pour refléter l'état d'Aura.

    API alignée sur `LedController.set_state(state)`. Ne lève JAMAIS : toute
    erreur (import, device absent, USB) → `available=False` et no-op. Toutes les
    écritures partent d'un thread worker alimenté par une file (latest-wins), la
    boucle audio n'est donc jamais bloquée par un transfert de contrôle USB.
    """

    def __init__(self, enabled: bool = False):
        self.available = False
        self._dev = None
        self._q: "queue.Queue" = queue.Queue()
        self._worker = None
        self._last_requested = None   # dédup à l'ENFILEMENT (changement d'état)
        self._applied = None          # dernier état RÉELLEMENT écrit
        self._fails = 0               # échecs d'écriture consécutifs
        self._chase_i = 0             # position de la comète du chenillard THINKING
        self._stop = False

        if not enabled:
            return
        if not _USB_OK:
            logger.info("[xvf-led] pyusb/libusb indisponible → anneau LED désactivé")
            return
        try:
            dev = usb.core.find(idVendor=_VID, idProduct=_PID)
        except Exception as e:                       # backend libusb manquant, etc.
            logger.info("[xvf-led] recherche USB impossible (%s) → anneau désactivé", e)
            return
        if dev is None:
            logger.info("[xvf-led] array %04x:%04x absent → anneau désactivé", _VID, _PID)
            return

        self._dev = dev
        self.available = True
        self._worker = threading.Thread(target=self._run, name="xvf-led", daemon=True)
        self._worker.start()
        atexit.register(self.close)
        logger.info("[xvf-led] anneau LED XVF3800 activé (état → couleur, DoA en écoute)")
        # Init : luminosité + état IDLE une seule fois (IDLE écrit déjà la luminosité).
        # ⚠️ AUCUN save_configuration/CLEAR_CONFIGURATION (resid 48) n'est JAMAIS émis.
        self.set_state("IDLE")

    # ── API publique (appelée depuis l'orchestrateur, thread audio) ──────
    def set_state(self, state: str):
        """Demande l'affichage d'un état. Non bloquant : enfile puis retourne.

        Dédup : on n'enfile QUE si l'état change (mitigation bug #18 = pas de
        martèlement). Le worker coalesce en plus les rafales (latest-wins).
        """
        if not self.available:
            return
        if state == self._last_requested:
            return                                   # même état → rien à faire
        self._last_requested = state
        if state not in _STATE_WRITES:
            logger.debug("[xvf-led] état inconnu %r → ignoré", state)
            return
        try:
            self._q.put_nowait(state)
        except Exception:
            pass

    def flash_error(self):
        """Flash d'erreur : pouls rouge ~1.5 s puis retour à l'état courant.
        Appelé avec les bips d'échec de l'orchestrateur (fins de tour anormales)."""
        if not self.available:
            return
        try:
            self._q.put_nowait(_ERROR_FLASH)
        except Exception:
            pass

    def close(self):
        """Arrêt propre : stoppe le worker et restaure l'effet doa (défaut usine)."""
        if not self.available:
            return
        self._stop = True
        try:
            self._q.put_nowait(_STOP)
        except Exception:
            pass
        if self._worker is not None:
            self._worker.join(timeout=1.0)
        # Best-effort : revenir à l'effet doa (comportement par défaut de la puce).
        try:
            self._write(*_LED_EFFECT, [_DOA])
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass
        self.available = False

    # ── Worker (thread dédié) ───────────────────────────────────────────
    def _run(self):
        # Correction gamma à l'init (teintes fidèles) — cosmétique : échec ignoré.
        try:
            self._write(*_LED_GAMMIFY, [1])
        except Exception:
            pass
        if _SELF_TEST:
            import time as _t
            logger.info("[xvf-led] AUTO-TEST : cycle de tous les états (~2 s chacun) — "
                        "IDLE bleu respirant, LISTENING vert+pointeur bleu (DoA), "
                        "THINKING chenillard orange, SPEAKING vert pulsé, "
                        "CONVERSING bleu+pointeur vert (DoA), MUTED rouge, ENROLLING blanc")
            for st in ("IDLE", "LISTENING", "THINKING", "SPEAKING",
                       "CONVERSING", "MUTED", "ENROLLING"):
                if self._stop or not self.available:
                    break
                self._apply(st)
                if st == "THINKING":          # l'auto-test montre la comète en mouvement
                    for _ in range(13):
                        self._chase_step()
                        _t.sleep(_CHASE_STEP_S)
                else:
                    _t.sleep(2.0)
            self._applied = None      # force la réécriture de l'état réel ensuite
        while True:
            # CHENILLARD : tant que l'état affiché est THINKING, on avance la
            # comète toutes les _CHASE_STEP_S en guettant un nouvel état (le
            # get(timeout) sert de métronome — aucune écriture hors THINKING).
            if self._applied == "THINKING" and not self._stop:
                try:
                    state = self._q.get(timeout=_CHASE_STEP_S)
                except queue.Empty:
                    self._chase_step()
                    continue
            else:
                state = self._q.get()
            if state is _STOP or self._stop:
                break
            # latest-wins : on vide la file et ne garde que le DERNIER état
            # (les états intermédiaires d'une rafale sont sautés).
            while True:
                try:
                    nxt = self._q.get_nowait()
                except queue.Empty:
                    break
                if nxt is _STOP:
                    self._stop = True
                    break
                state = nxt
            if self._stop or not self.available:
                break
            if state == _ERROR_FLASH:
                # Pouls rouge bref, puis retour au DERNIER état demandé (le flash
                # ne doit jamais rester : une erreur est un événement, pas un état).
                self._apply("ERROR")
                time.sleep(1.5)
                self._applied = None
                state = self._last_requested if self._last_requested in _STATE_WRITES else "IDLE"
            if state == self._applied:
                continue                             # déjà affiché → rien à écrire
            self._apply(state)

    def _chase_step(self):
        """Une frame du chenillard THINKING : comète orange (1 pleine + 2 de
        traîne) qui avance d'une LED. Recette utilisateur 24/07 (sonde validée
        à l'œil). Mêmes garde-fous d'échec que _apply."""
        cols = []
        for k in range(12):
            d = (k - self._chase_i) % 12
            cols.append(_rgb(_ORANGE) if d == 0 else (_rgb(_TRAINE) if d <= 2 else 0))
        try:
            self._write(*_LED_RING, cols)
            self._fails = 0
            self._chase_i = (self._chase_i + 1) % 12
        except Exception as e:
            self._fails += 1
            logger.debug("[xvf-led] chenillard : écriture échouée (%d/%d) : %s",
                         self._fails, _MAX_FAILS, e)
            if self._fails >= _MAX_FAILS:
                self.available = False
                logger.warning("[xvf-led] %d échecs consécutifs → anneau désactivé", _MAX_FAILS)

    def _apply(self, state: str):
        writes = _STATE_WRITES.get(state)
        if not writes:
            return
        if state == "THINKING":
            self._chase_i = 0        # la comète repart toujours de la LED 0
        for (resid, cmdid, dtype), values in writes:
            if not self.available:
                return
            try:
                self._write(resid, cmdid, dtype, values)
                self._fails = 0                      # succès → compteur d'échecs remis à 0
            except Exception as e:
                self._fails += 1
                logger.debug("[xvf-led] écriture échouée (%d/%d) état=%s : %s",
                             self._fails, _MAX_FAILS, state, e)
                if self._fails >= _MAX_FAILS:
                    self.available = False
                    logger.warning("[xvf-led] %d échecs consécutifs → anneau désactivé "
                                   "(plus AUCUNE écriture ; device débranché/cassé ?)",
                                   _MAX_FAILS)
                return                               # abandonne les écritures restantes
        self._applied = state
        if state == "THINKING" and self.available:
            self._chase_step()                       # première frame sans attendre le métronome

    # ── Transfert de contrôle vendor (réplique de ReSpeaker.write de xvf_host.py) ──
    def _write(self, resid: int, cmdid: int, dtype: str, values):
        payload = bytearray()
        if dtype == "uint8":
            for v in values:
                payload += int(v).to_bytes(1, byteorder="little")
        elif dtype == "uint32":
            for v in values:
                # xvf_host.py utilise struct.pack('I', v) (ordre natif) ; '<I' =
                # little-endian explicite, identique sur Pi/x86 et conforme au §2.2.
                payload += struct.pack("<I", int(v) & 0xFFFFFFFF)
        else:                                        # pragma: no cover
            raise ValueError("type non géré: %s" % dtype)
        # bmRequestType=0x40, bRequest=0, wValue=cmdid, wIndex=resid — cf. xvf_host.py.
        self._dev.ctrl_transfer(_BM_REQUEST_OUT, 0, cmdid, resid,
                                bytes(payload), _TIMEOUT_MS)

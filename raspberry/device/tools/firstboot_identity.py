"""Identité premier boot de l'enceinte (plug-and-play chantier 3).

Chaque enceinte sortie de l'image golden est un CLONE parfait de l'image
maître : ce script la rend UNIQUE au tout premier démarrage — DEVICE_TOKEN
aléatoire (l'identité auprès du backend, révocable) + n° de série court
AUR-XXXXX (étiquette/QR atelier) — puis l'enrôle auprès du backend : POST
/api/device/register → ligne `stock` de la table devices (chantier 2,
docs/PLUG-AND-PLAY.md).

Contrat (lancé par le service oneshot aura-firstboot, User=pi) :
  - ~/.aura/.identity_done présent → tout est déjà fait, exit 0 immédiat
    (le service porte en plus ConditionPathExists : double sécurité) ;
  - identité générée + register OK → écrit le marqueur AVEC le serial dedans
    (l'atelier le lit pour imprimer l'étiquette QR), exit 0 ;
  - réseau/backend KO → exit 1 SANS marqueur : systemd relance
    (Restart=on-failure) jusqu'à réussir — l'enceinte finira par avoir
    Internet (portail Wi-Fi) et DOIT s'enrôler toute seule.

Idempotence (le point dur) : l'identité est PERSISTÉE dans ~/.aura/env AVANT
le premier POST. Un register à moitié réussi (réponse perdue, reboot au
mauvais moment) ne régénère donc JAMAIS un nouveau token au retry — on
re-POST la MÊME identité, et le register backend est idempotent. On ne
touche jamais à un DEVICE_TOKEN existant (enceinte déjà provisionnée à la
main = on la laisse tranquille, on complète juste ce qui manque).

Stdlib uniquement, lancé par le python3 SYSTÈME (pas le venv) : l'identité
doit se créer même si le venv de l'orchestrateur est cassé — même philosophie
que wifi_portal.py.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aura-firstboot")

# Chemin EXPLICITE (pas ~) : un lancement de debug via sudo donnerait /root et
# créerait une identité fantôme au mauvais endroit. AURA_HOME surchargeable
# pour les bancs de dev hors Pi.
AURA_DIR = Path(os.getenv("AURA_HOME", "/home/pi/.aura"))
ENV_FILE = AURA_DIR / "env"
DONE_FILE = AURA_DIR / ".identity_done"

# Alphabet SANS caractères ambigus (pas de 0/O, 1/I/L) : le serial est lu à
# voix haute au téléphone et recopié à la main sur l'étiquette — chaque
# confusion possible est un ticket support.
SERIAL_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
SERIAL_LEN = 5

# Même variable d'env que device/config.py / device/cloud.py (CLOUD_BACKEND_URL)
# — surtout PAS un deuxième nom pour la même chose. Défaut = la prod (le défaut
# localhost de config.py n'a aucun sens sur une enceinte sortie d'usine).
DEFAULT_BACKEND = "https://backend-aura.hallia.ai"

REGISTER_PATH = "/api/device/register"
HTTP_TIMEOUT_S = 15
RETRIES = 3
BACKOFF_S = (5, 15)              # attentes ENTRE les 3 tentatives (backoff ×3)

# Config par défaut écrite dans ~/.aura/env (seulement les clés ABSENTES —
# jamais d'écrasement). POURQUOI ces valeurs :
#  - audio par NOMS stables (et pas plughw:3 qui dérive selon l'ordre
#    d'énumération USB au boot — cf. device/systemd/99-aura-alsa.rules) ;
#  - canaux/gain = réglages XVF3800 validés (docs/hardware/respeaker-xvf3800.md
#    §3) : ch1 = flux ASR, gain 6.0 car ce canal sort ~40× plus bas ;
#  - AEC_ENABLED=0 : l'AEC est dans le matériel de l'array, PAS dans PipeWire ;
#  - XVF_LED=1 : l'anneau de l'array reflète l'état d'Aura (produit fini) ;
#  - STREAMING_MODE=1 : chemin temps réel (Deepgram Flux) = le mode de prod ;
#  - WAKE_SAVE_AUDIO=0 : la récolte terrain A/B est un outil de dev, pas un
#    comportement d'usine (vie privée + usure de la carte SD).
ENV_DEFAULTS: list[tuple[str, str]] = [
    ("CLOUD_BACKEND_URL", DEFAULT_BACKEND),
    ("AUDIO_INPUT_DEVICE", "reSpeaker"),
    ("AUDIO_INPUT_CHANNELS", "2"),
    ("AUDIO_INPUT_CHANNEL", "1"),
    ("AUDIO_INPUT_GAIN", "6.0"),
    ("AUDIO_OUTPUT_DEVICE", "plughw:Array"),
    ("AEC_ENABLED", "0"),
    ("XVF_LED", "1"),
    ("STREAMING_MODE", "1"),
    ("WAKE_SAVE_AUDIO", "0"),
]


def _read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE (même format tolérant que device/config.py)."""
    values: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    except Exception as e:                     # fichier corrompu → on complète quand même
        logger.warning("lecture %s : %s", path, e)
    return values


def _gen_serial() -> str:
    return "AUR-" + "".join(secrets.choice(SERIAL_ALPHABET) for _ in range(SERIAL_LEN))


def _replace_env_var(key: str, value: str):
    """Remplace (ou ajoute) une variable dans ~/.aura/env — atomique.
    Sert à la régénération de serial sur collision (409)."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    tmp = ENV_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(ENV_FILE)
    os.chmod(ENV_FILE, 0o600)


def _append_env(pairs: list[tuple[str, str]]):
    """COMPLÈTE ~/.aura/env (append, jamais de réécriture : les éditions
    manuelles d'un technicien restent intactes octet pour octet)."""
    AURA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    block = "" if not existing or existing.endswith("\n") else "\n"
    if not existing:
        block += "# Config de l'enceinte Aura — générée au premier boot (firstboot_identity).\n"
    block += "".join(f"{k}={v}\n" for k, v in pairs)
    with open(ENV_FILE, "a") as f:
        f.write(block)
    # 600 : le fichier contient DEVICE_TOKEN (identité du device).
    os.chmod(ENV_FILE, 0o600)


def _register(backend: str, device_token: str, serial: str) -> bool | str:
    """POST {device_token, serial} avec retries. Le register backend est
    idempotent (chantier 2) : re-POSTer la même identité est toujours sûr.
    Renvoie True (OK), False (échec réseau) ou "serial_conflict" (409 : le
    serial est déjà pris — l'appelant en régénère un, revue 27/07)."""
    url = backend.rstrip("/") + REGISTER_PATH
    payload = json.dumps({"device_token": device_token, "serial": serial}).encode()
    headers = {"Content-Type": "application/json"}
    # Secret d'usine : env d'abord, sinon /etc/aura/factory.env — ce fichier
    # système SURVIT à la purge de ~/.aura (anonymisation de l'image golden),
    # contrairement à ~/.aura/env. Sans lui, le register renverrait 401.
    secret = os.environ.get("FACTORY_REGISTER_SECRET", "")
    if not secret:
        try:
            for line in Path("/etc/aura/factory.env").read_text().splitlines():
                if line.startswith("FACTORY_REGISTER_SECRET="):
                    secret = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    if secret:
        headers["X-Factory-Secret"] = secret
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                if 200 <= resp.status < 300:
                    logger.info("enrôlement OK (%s, HTTP %d)", serial, resp.status)
                    return True
                logger.warning("register HTTP %d (essai %d/%d)", resp.status, attempt, RETRIES)
        except urllib.error.HTTPError as e:
            if e.code == 409:                  # serial déjà pris → régénérer
                logger.warning("serial %s déjà utilisé (409) → régénération", serial)
                return "serial_conflict"
            # 4xx/5xx : on logge le corps (message d'erreur backend) — précieux
            # à l'atelier — puis on retente : un 502/503 de déploiement passe.
            body = ""
            try:
                body = e.read().decode(errors="replace")[:200]
            except Exception:
                pass
            logger.warning("register HTTP %d (essai %d/%d) : %s", e.code, attempt, RETRIES, body)
        except Exception as e:                 # URLError, timeout, DNS…
            logger.warning("register injoignable (essai %d/%d) : %s", attempt, RETRIES, e)
        if attempt < RETRIES:
            wait = BACKOFF_S[attempt - 1]
            logger.info("nouvelle tentative dans %d s", wait)
            time.sleep(wait)
    return False


def main() -> int:
    if DONE_FILE.exists():
        logger.info("identité déjà établie (%s) — rien à faire", DONE_FILE)
        return 0

    env = _read_env(ENV_FILE)

    # Identité : réutiliser l'existante si présente (idempotence), sinon générer.
    token = env.get("DEVICE_TOKEN") or ""
    serial = env.get("AURA_SERIAL") or ""
    missing: list[tuple[str, str]] = []
    if not token:
        token = secrets.token_urlsafe(32)
        missing.append(("DEVICE_TOKEN", token))
        logger.info("nouveau DEVICE_TOKEN généré")
    else:
        logger.info("DEVICE_TOKEN existant conservé (jamais écrasé)")
    if not serial:
        serial = _gen_serial()
        missing.append(("AURA_SERIAL", serial))
    logger.info("n° de série : %s", serial)

    # Config par défaut : uniquement les clés absentes.
    missing += [(k, v) for k, v in ENV_DEFAULTS if k not in env]

    # PERSISTER AVANT le POST : si le register réussit côté backend mais que la
    # réponse se perd (ou coupure de courant), le retry repartira avec la MÊME
    # identité au lieu de créer un doublon.
    if missing:
        _append_env(missing)
        logger.info("~/.aura/env complété (%d clé(s) : %s)",
                    len(missing), ", ".join(k for k, _ in missing))

    backend = (os.environ.get("CLOUD_BACKEND_URL")
               or env.get("CLOUD_BACKEND_URL") or DEFAULT_BACKEND)
    result = _register(backend, token, serial)
    # Collision de serial (1 chance sur ~23M par paire, mais une flotte
    # entière finit par la voir) : régénérer et re-persister, 3 tentatives.
    regen = 0
    while result == "serial_conflict" and regen < 3:
        regen += 1
        serial = _gen_serial()
        _replace_env_var("AURA_SERIAL", serial)
        logger.info("nouveau serial généré : %s (tentative %d/3)", serial, regen)
        result = _register(backend, token, serial)
    if result is not True:
        # PAS de marqueur : le service systemd relancera jusqu'à ce que
        # l'enrôlement aboutisse (l'identité, elle, est déjà persistée).
        logger.error("enrôlement impossible (%s) — nouvel essai au prochain lancement", backend)
        return 1

    # Marqueur = serial en clair : l'atelier fait `cat .identity_done` pour
    # imprimer l'étiquette QR (cf. docs/IMAGE-GOLDEN.md).
    DONE_FILE.write_text(serial + "\n")
    logger.info("identité établie et enrôlée — %s", serial)
    return 0


if __name__ == "__main__":
    sys.exit(main())

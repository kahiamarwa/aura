#!/usr/bin/env python3
"""update_agent.py — mise à jour SIGNÉE de l'enceinte (cf. docs/FLEET-SECURITY.md).

Remplace l'ancienne version git-based : l'enceinte ne connaît plus GitHub. Elle
demande au VPS son manifeste (auth DEVICE_TOKEN), télécharge un paquet, VÉRIFIE
son sha256 ET sa signature Ed25519 avec la clé publique embarquée ci-dessous,
puis l'installe de façon atomique avec rollback si l'enceinte ne repart pas.

Aucun credential de code sur le Pi ; un paquet piraté est rejeté (signature).

Lancé par systemd (aura-update.service/.timer) ou l'ordre distant « update ».
  python update_agent.py            # cycle complet
  python update_agent.py --check    # dry-run : montre courante/cible
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aura-update")

# Clé PUBLIQUE Ed25519 de Hallia (raw 32 octets, hex). Vérifie les releases.
# Publique par nature : sa présence ici n'expose rien. La privée signe côté
# Hallia et n'est JAMAIS sur le Pi. (docs/FLEET-SECURITY.md)
RELEASE_PUBKEY_HEX = "d020c1e8fa9a5a7e7d26af914958284f3c76cdcf6ddfe8d2f5c651d14ca313e8"

# /home/pi en dur (pas Path.home()) : l'agent tourne en ROOT (systemd/sudo) où
# Path.home()=/root — il doit lire l'env et écrire la version de l'utilisateur pi.
HOME = Path(os.environ.get("AURA_HOME", "/home/pi"))
ENV_FILE = HOME / ".aura" / "env"
VERSION_FILE = HOME / ".aura" / "version"
DEVICE_DIR = Path(os.environ.get("AURA_DEVICE_DIR", "/home/pi/aura/raspberry/device"))
RELEASES_DIR = HOME / "aura_releases"
SERVICE = "aura"
HEALTH_TIMEOUT_S = 120
HEALTH_HOLD_S = 30


def _env(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


def _current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "dev"
    except Exception:
        return "dev"


def _headers() -> dict:
    tok = _env("DEVICE_TOKEN")
    if not tok:
        raise SystemExit("DEVICE_TOKEN absent — enceinte non provisionnée")
    return {"X-Device-Token": tok}


def _fetch_manifest() -> dict:
    backend = _env("CLOUD_BACKEND_URL", "https://backend-aura.hallia.ai")
    req = urllib.request.Request(backend.rstrip("/") + "/api/device/update-manifest",
                                 headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _verify_signature(sha256_hex: str, signature_hex: str) -> bool:
    """Vérifie Ed25519(sha256_bytes) avec la clé publique embarquée. cryptography
    si présent, sinon openssl CLI (toujours là). Défaut : REFUS."""
    msg = bytes.fromhex(sha256_hex)
    sig = bytes.fromhex(signature_hex)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(RELEASE_PUBKEY_HEX)).verify(sig, msg)
        return True
    except ImportError:
        pass
    except Exception:
        return False
    try:
        import base64
        der = bytes.fromhex("302a300506032b6570032100") + bytes.fromhex(RELEASE_PUBKEY_HEX)
        pem = ("-----BEGIN PUBLIC KEY-----\n"
               + base64.encodebytes(der).decode() + "-----END PUBLIC KEY-----\n")
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            (t / "pub.pem").write_text(pem)
            (t / "msg").write_bytes(msg)
            (t / "sig").write_bytes(sig)
            r = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(t / "pub.pem"),
                 "-rawin", "-in", str(t / "msg"), "-sigfile", str(t / "sig")],
                capture_output=True, text=True, timeout=15)
            return r.returncode == 0
    except Exception as e:
        logger.error("verif signature (openssl) impossible : %s", e)
        return False


def _service_ok() -> bool:
    return subprocess.run(["systemctl", "is-active", SERVICE],
                          capture_output=True, text=True).stdout.strip() == "active"


def _restarts() -> int:
    r = subprocess.run(["systemctl", "show", "-p", "NRestarts", "--value", SERVICE],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except Exception:
        return 0


def _health_check() -> bool:
    """Actif ET tient HEALTH_HOLD_S sans redémarrer (sinon crash-loop → rollback)."""
    subprocess.run(["systemctl", "restart", SERVICE], check=False)
    deadline = time.time() + HEALTH_TIMEOUT_S
    while time.time() < deadline:
        if _service_ok():
            base = _restarts()
            time.sleep(HEALTH_HOLD_S)
            if _service_ok() and _restarts() == base:
                return True
        time.sleep(3)
    return False


def _apply(tar_bytes: bytes, version: str) -> bool:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    backup = RELEASES_DIR / f"backup_{int(time.time())}"
    shutil.copytree(DEVICE_DIR, backup,
                    ignore=shutil.ignore_patterns("venv", "__pycache__"))
    logger.info("backup du code courant -> %s", backup.name)
    req_path = DEVICE_DIR / "requirements.txt"
    req_before = req_path.read_bytes() if req_path.exists() else b""
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise RuntimeError(f"chemin de paquet suspect: {m.name}")
            tar.extractall(DEVICE_DIR)
        req_after = req_path.read_bytes() if req_path.exists() else b""
        if req_after and req_after != req_before:
            logger.info("requirements.txt modifie -> pip install")
            subprocess.run([str(DEVICE_DIR / "venv" / "bin" / "pip"), "install",
                            "-r", str(req_path)], check=True, timeout=600)
        if _health_check():
            VERSION_FILE.write_text(version + "\n")
            logger.info("OK mise a jour %s appliquee et saine", version)
            shutil.rmtree(backup, ignore_errors=True)
            return True
        raise RuntimeError("controle de sante echoue apres bascule")
    except Exception as e:
        logger.error("ROLLBACK (%s) : %s", version, e)
        for item in backup.iterdir():
            dst = DEVICE_DIR / item.name
            if item.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        subprocess.run(["systemctl", "restart", SERVICE], check=False)
        shutil.rmtree(backup, ignore_errors=True)
        logger.info("code precedent restaure")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry-run : courante/cible")
    args = ap.parse_args()
    current = _current_version()
    try:
        man = _fetch_manifest()
    except Exception as e:
        logger.error("manifeste injoignable : %s", e)
        return 1
    target = man.get("version")
    channel = man.get("channel", "stable")
    if not target:
        logger.info("aucune release publiee pour le canal << %s >> - rien a faire", channel)
        return 0
    logger.info("courante : %s | cible (%s) : %s", current, channel, target)
    if args.check:
        logger.info("(dry-run) %s", "a jour" if target == current else "MAJ disponible")
        return 0
    if target == current:
        return 0
    try:
        with urllib.request.urlopen(man["url"], timeout=120) as r:
            tar_bytes = r.read()
    except Exception as e:
        logger.error("telechargement du paquet KO : %s", e)
        return 1
    sha = hashlib.sha256(tar_bytes).hexdigest()
    if sha != man.get("sha256"):
        logger.error("sha256 NE CORRESPOND PAS - paquet rejete")
        return 1
    if not _verify_signature(sha, man.get("signature", "")):
        logger.error("SIGNATURE INVALIDE - paquet rejete (ni Hallia ni integre)")
        return 1
    logger.info("paquet verifie (sha256 + signature Ed25519) - installation")
    return 0 if _apply(tar_bytes, target) else 1


if __name__ == "__main__":
    raise SystemExit(main())

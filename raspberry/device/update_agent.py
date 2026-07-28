"""Agent de mise à jour de flotte (plug-and-play chantier 4).

Le canal de distribution = les TAGS git du miroir Pi (/home/pi/aura) :
  - `stable` suit le plus grand `vX.Y.Z`      (ex. v2.1.0)
  - `beta`   suit le plus grand `vX.Y.Z-beta.N` OU la stable si plus récente
    (sémantique semver : v2.1.0-beta.3 < v2.1.0 — une beta ne « bat » que les
    versions stables STRICTEMENT inférieures).
La branche de dev n'atteint JAMAIS un client : on ne checkout que des tags.

Séquence (lancée par aura-update.timer chaque nuit à ~03h00, ou à la main) :
  fetch --tags → cible du canal → si ≠ version courante :
  checkout du tag → pip install -r si requirements.txt du device a changé
  entre les deux versions → systemctl restart aura → CONTRÔLE DE SANTÉ
  (service actif sous 120 s PUIS il tient 30 s sans redémarrage — NRestarts
  stable) → échec ⇒ ROLLBACK au point de départ + restart.
  La version courante est mise en cache dans ~/.aura/version (remontée au
  backend par le battement, chantier 5).

Privilèges : le service tourne en ROOT (systemctl restart oblige) MAIS toutes
les commandes git et pip sont RE-DÉLÉGUÉES à l'utilisateur pi via runuser —
sinon root sèmerait des fichiers .git/ et site-packages/ à lui dans le dépôt
et le venv de pi, et tout git/pip ultérieur lancé par pi casserait (sans
compter le refus « dubious ownership » de git en root sur un dépôt de pi).

Garde-fou : refuse de tourner si le dépôt a des modifications locales
(enceinte de dev, hotfix manuel en cours) — une MAJ automatique qui écrase du
travail non commité est inexcusable.

Codes retour : 0 = à jour ou MAJ réussie ; 1 = échec (réseau, checkout, ou
santé KO → rollback tenté) ; 2 = précondition refusée (dépôt sale, outillage
absent). Mode --check : dry-run, affiche courante/cible sans rien toucher.

Stdlib uniquement (lancé par le python3 système : l'agent doit pouvoir
RÉPARER un venv cassé, donc ne jamais en dépendre).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aura-update")

# Chemins EXPLICITES (pas ~) : le service tourne en root, ~ vaudrait /root.
REPO = Path(os.getenv("AURA_REPO_DIR", "/home/pi/aura"))
# Layout du miroir Pi : le code device vit sous raspberry/ (chemin RELATIF au
# dépôt — c'est lui qu'on donne à git diff).
DEVICE_REQS = "raspberry/device/requirements.txt"
VENV_PIP = os.getenv("AURA_VENV_PIP", "/home/pi/aura/raspberry/device/venv/bin/pip")
SERVICE = os.getenv("AURA_SERVICE", "aura")
AURA_DIR = Path(os.getenv("AURA_HOME", "/home/pi/.aura"))
VERSION_FILE = AURA_DIR / "version"
REPO_USER = os.getenv("AURA_REPO_USER", "pi")

HEALTH_TIMEOUT_S = 120     # délai max pour voir le service « active »
HEALTH_HOLD_S = 30         # puis il doit TENIR 30 s sans redémarrage

_STABLE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_BETA_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-beta\.(\d+)$")


# ── exécution de commandes ───────────────────────────────────────────
def _as_repo_user(cmd: list[str]) -> list[str]:
    """git/pip s'exécutent comme pi même quand l'agent tourne en root (cf.
    docstring : propriété des fichiers + dubious ownership)."""
    if os.geteuid() == 0 and REPO_USER:
        return ["runuser", "-u", REPO_USER, "--", *cmd]
    return cmd


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return _run(_as_repo_user(["git", "-C", str(REPO), *args]), timeout=timeout)


# ── versions & tags ──────────────────────────────────────────────────
def _tag_key(tag: str, channel: str) -> tuple | None:
    """Clé de tri NUMÉRIQUE d'un tag pour un canal donné (None = hors canal).

    Tri par tuples d'entiers, PAS alphabétique : v2.10.0 > v2.9.0 (le tri
    lexicographique dirait l'inverse). Le 4e champ encode la sémantique
    semver : stable (1) > beta (0) À VERSION ÉGALE, une beta d'une version
    SUPÉRIEURE bat une stable inférieure.
    """
    m = _STABLE_RE.match(tag)
    if m:
        return (int(m[1]), int(m[2]), int(m[3]), 1, 0)
    if channel == "beta":
        m = _BETA_RE.match(tag)
        if m:
            return (int(m[1]), int(m[2]), int(m[3]), 0, int(m[4]))
    return None


def _latest_tag(channel: str) -> str | None:
    r = _git("tag", "--list", "v*")
    if r.returncode != 0:
        logger.error("git tag : %s", r.stderr.strip())
        return None
    candidates = [(k, t) for t in r.stdout.split()
                  if (k := _tag_key(t, channel)) is not None]
    if not candidates:
        return None
    return max(candidates)[1]


def _current_version() -> str:
    """Tag EXACT du HEAD, sinon « dev » (enceinte de dev sur une branche —
    l'agent la considérera « pas à jour » et la ramènera sur un tag, sauf si
    le garde-fou dépôt sale l'en empêche d'abord)."""
    r = _git("describe", "--tags", "--exact-match", "HEAD")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "dev"


def _write_version(version: str):
    """Cache ~/.aura/version (le battement le remonte au backend, chantier 5)."""
    try:
        AURA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        VERSION_FILE.write_text(version + "\n")
        if os.geteuid() == 0:                  # fichier écrit par root chez pi → rendre à pi
            import pwd
            u = pwd.getpwnam(REPO_USER)
            os.chown(VERSION_FILE, u.pw_uid, u.pw_gid)
    except Exception as e:
        logger.warning("écriture %s : %s", VERSION_FILE, e)


def _read_channel() -> str:
    """UPDATE_CHANNEL depuis l'environnement puis ~/.aura/env (défaut stable).
    Chemin explicite /home/pi : le service tourne en root."""
    ch = os.environ.get("UPDATE_CHANNEL", "")
    if not ch:
        try:
            for line in (AURA_DIR / "env").read_text().splitlines():
                if line.strip().startswith("UPDATE_CHANNEL="):
                    ch = line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    ch = (ch or "stable").lower()
    if ch not in ("stable", "beta"):
        logger.warning("canal inconnu « %s » → stable", ch)
        ch = "stable"
    return ch


# ── étapes de la MAJ ─────────────────────────────────────────────────
def _reqs_changed(old_ref: str, new_ref: str) -> bool:
    """Vrai si requirements.txt du device diffère entre les deux versions —
    seul cas où un pip install (long, et risqué hors ligne partiel) se justifie."""
    r = _git("diff", "--name-only", f"{old_ref}..{new_ref}", "--", DEVICE_REQS)
    if r.returncode != 0:
        # Dans le doute (ref exotique), installer : un pip install superflu est
        # bénin, des dépendances manquantes cassent le boot.
        logger.warning("git diff requirements : %s — pip install par précaution",
                       r.stderr.strip())
        return True
    return bool(r.stdout.strip())


def _pip_install() -> bool:
    logger.info("requirements modifiés → pip install -r %s", DEVICE_REQS)
    r = _run(_as_repo_user([VENV_PIP, "install", "-r", str(REPO / DEVICE_REQS)]),
             timeout=900)
    if r.returncode != 0:
        logger.error("pip install : %s", (r.stderr or r.stdout).strip()[-500:])
        return False
    return True


def _checkout(ref: str) -> bool:
    r = _git("checkout", "--quiet", ref)
    if r.returncode != 0:
        logger.error("git checkout %s : %s", ref, r.stderr.strip())
        return False
    return True


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", *args], timeout=90)


def _svc_prop(prop: str) -> str:
    r = _systemctl("show", "-p", prop, "--value", SERVICE)
    return r.stdout.strip()


def _service_healthy() -> bool:
    """Le service est « active » sous HEALTH_TIMEOUT_S, PUIS tient
    HEALTH_HOLD_S sans redémarrage.

    Pourquoi les deux phases : aura.service a Restart=always — un orchestrateur
    qui crashe en boucle (import cassé, modèle manquant) repasse « active »
    quelques secondes à chaque cycle. « is-active » seul dirait OK au mauvais
    moment. On fige donc NRestarts une fois actif et on vérifie qu'il n'a PAS
    bougé après 30 s : un service sain ne redémarre pas.
    """
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        r = _systemctl("is-active", "--quiet", SERVICE)
        if r.returncode == 0:
            break
        time.sleep(3)
    else:
        logger.error("santé : « %s » jamais actif en %d s", SERVICE, HEALTH_TIMEOUT_S)
        return False
    restarts_before = _svc_prop("NRestarts")
    logger.info("santé : actif — observation %d s (NRestarts=%s)…",
                HEALTH_HOLD_S, restarts_before or "?")
    hold_end = time.monotonic() + HEALTH_HOLD_S
    while time.monotonic() < hold_end:
        time.sleep(5)
        if _systemctl("is-active", "--quiet", SERVICE).returncode != 0:
            logger.error("santé : le service est retombé pendant l'observation")
            return False
    restarts_after = _svc_prop("NRestarts")
    if restarts_after != restarts_before:
        logger.error("santé : redémarrage détecté (NRestarts %s → %s)",
                     restarts_before, restarts_after)
        return False
    logger.info("santé : OK (actif et stable %d s)", HEALTH_HOLD_S)
    return True


def _rollback(rollback_ref: str, rollback_label: str, failed_tag: str):
    """Retour au point de départ. Best-effort assumé : à ce stade on préfère
    une enceinte sur l'ancienne version (état connu-bon) à tout héroïsme."""
    logger.error("ROLLBACK vers %s (la MAJ %s a échoué le contrôle de santé)",
                 rollback_label, failed_tag)
    if not _checkout(rollback_ref):
        logger.critical("ROLLBACK IMPOSSIBLE (checkout) — intervention manuelle requise")
        return
    if _reqs_changed(failed_tag, rollback_ref) and not _pip_install():
        logger.critical("ROLLBACK : pip a échoué — le venv peut être incohérent")
    _systemctl("restart", SERVICE)
    if _service_healthy():
        logger.info("ROLLBACK terminé : %s de nouveau en service sur %s",
                    SERVICE, rollback_label)
    else:
        logger.critical("ROLLBACK : le service reste KO — intervention manuelle requise")


# ── programme principal ──────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Agent de mise à jour Aura (tags git)")
    parser.add_argument("--check", action="store_true",
                        help="dry-run : affiche version courante et cible, ne touche à rien")
    args = parser.parse_args()

    if not (REPO / ".git").exists():
        logger.error("dépôt introuvable : %s", REPO)
        return 2

    channel = _read_channel()
    logger.info("canal : %s (dépôt %s)", channel, REPO)

    # Garde-fou AVANT tout : des modifs locales = enceinte de dev ou hotfix en
    # cours → un checkout automatique détruirait ce travail. On refuse net.
    r = _git("status", "--porcelain")
    dirty = bool(r.stdout.strip())
    if dirty and not args.check:
        logger.error("REFUS : le dépôt a des modifications locales — MAJ automatique "
                     "annulée (commitez/stashez, ou nettoyez l'enceinte) :\n%s",
                     r.stdout.strip()[:800])
        return 2

    r = _git("fetch", "--tags", timeout=180)
    if r.returncode != 0:
        logger.error("git fetch --tags : %s", r.stderr.strip())
        return 1

    target = _latest_tag(channel)
    current = _current_version()
    if target is None:
        logger.info("aucun tag publié pour le canal « %s » — rien à faire", channel)
        return 0

    if args.check:
        logger.info("courante : %s | cible (%s) : %s%s%s",
                    current, channel, target,
                    " — à jour" if current == target else " — MAJ disponible",
                    " | ⚠ dépôt sale (la MAJ réelle refusera)" if dirty else "")
        return 0

    if current == target:
        logger.info("déjà à jour (%s)", current)
        _write_version(current)               # rafraîchit le cache (battement)
        return 0

    # Point de retour du rollback : le SHA (et pas le tag) — couvre aussi le
    # cas « dev » où HEAD n'est sur aucun tag.
    rollback_sha = _git("rev-parse", "HEAD").stdout.strip()
    rollback_label = current if current != "dev" else f"dev ({rollback_sha[:9]})"
    logger.info("MAJ %s → %s", current, target)

    if not _checkout(target):
        return 1
    if _reqs_changed(rollback_sha, target) and not _pip_install():
        _rollback(rollback_sha, rollback_label, target)
        return 1

    logger.info("redémarrage de %s…", SERVICE)
    r = _systemctl("restart", SERVICE)
    if r.returncode != 0:
        logger.error("systemctl restart : %s", r.stderr.strip())
        _rollback(rollback_sha, rollback_label, target)
        return 1

    if not _service_healthy():
        _rollback(rollback_sha, rollback_label, target)
        return 1

    _write_version(target)
    logger.info("MAJ RÉUSSIE : %s (canal %s)", target, channel)
    return 0


if __name__ == "__main__":
    sys.exit(main())

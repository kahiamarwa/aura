"""Portail captif Wi-Fi d'Aura — provisioning réseau plug-and-play (chantier 1).

Quand l'enceinte n'a pas d'Internet, elle devient son propre point d'accès
« Aura-Config » : le client s'y connecte avec son téléphone, une page s'ouvre
automatiquement (portail captif), il choisit son Wi-Fi d'entreprise et tape le
mot de passe. Chaque étape est annoncée à voix haute (WAV pré-enregistrés dans
assets/voice/ — le cloud TTS est inaccessible hors ligne, par définition).

Architecture (service systemd dédié, root, indépendant de l'orchestrateur —
il doit fonctionner même si Aura est plantée) :
  boucle de surveillance (60 s hors ligne → portail) → AP NetworkManager
  (nmcli hotspot, IP 10.42.0.1) → serveur HTTP stdlib port 80 (page + captive
  redirects Android/iOS) → tentative de connexion → succès : retour
  surveillance / échec : profil purgé + AP relancé.

Dépendances : NetworkManager (Pi OS Bookworm/Trixie = défaut) + aplay.
DNS captif : /etc/NetworkManager/dnsmasq-shared.d/aura-captive.conf
(address=/#/10.42.0.1) — installé avec le service (cf. device/systemd/).

⚠️ Radio unique : connecter wlan0 au Wi-Fi cible COUPE l'AP — le téléphone perd
la page avant de connaître le résultat. Assumé et annoncé (voix + page) : le
verdict est vocal, et l'AP revient tout seul en cas d'échec.
"""
from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aura-wifi")

AP_SSID = os.getenv("AURA_AP_SSID", "Aura-Config")
AP_CON = "aura-config"                 # nom du profil NetworkManager de l'AP
AP_IP = "10.42.0.1"
IFACE = os.getenv("AURA_WIFI_IFACE", "wlan0")
OFFLINE_GRACE_S = int(os.getenv("AURA_OFFLINE_GRACE_S", "60"))
CHECK_EVERY_S = 15
CONNECT_TIMEOUT_S = 45

_HERE = Path(__file__).resolve().parent
VOICE_DIR = _HERE / "assets" / "voice"


# ── utilitaires système ──────────────────────────────────────────────
def _nmcli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def _audio_device() -> str | None:
    """AUDIO_OUTPUT_DEVICE depuis ~pi/.aura/env (service root → chemin explicite)."""
    for env_path in (Path("/home/pi/.aura/env"), Path.home() / ".aura" / "env"):
        try:
            for line in env_path.read_text().splitlines():
                if line.strip().startswith("AUDIO_OUTPUT_DEVICE="):
                    return line.split("=", 1)[1].strip() or None
        except Exception:
            continue
    return None


def say(name: str):
    """Joue une annonce pré-enregistrée (best-effort, jamais bloquant > 30 s)."""
    wav = VOICE_DIR / f"{name}.wav"
    if not wav.exists():
        logger.warning("annonce absente: %s", wav)
        return
    cmd = ["aplay", "-q"]
    dev = _audio_device()
    if dev:
        cmd += ["-D", dev]
    try:
        subprocess.run(cmd + [str(wav)], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning("aplay: %s", e)


def has_connectivity() -> bool:
    """Vrai si Internet joignable (NetworkManager: full). 'portal'/'limited'/'none' → faux."""
    try:
        r = _nmcli("networking", "connectivity", "check", timeout=20)
        return r.stdout.strip() == "full"
    except Exception:
        return False


def scan_networks() -> list[dict]:
    """Réseaux visibles, triés par signal, dédupliqués par SSID (fait AVANT l'AP :
    une fois en mode point d'accès, la radio ne scanne plus)."""
    try:
        _nmcli("device", "wifi", "rescan", timeout=20)
        time.sleep(3)
        r = _nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", timeout=20)
    except Exception:
        return []
    seen: dict[str, dict] = {}
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid, signal, security = parts[0], parts[1], ":".join(parts[2:])
        if not ssid or ssid == AP_SSID:
            continue
        entry = {"ssid": ssid, "signal": int(signal or 0), "secured": security not in ("", "--")}
        if ssid not in seen or entry["signal"] > seen[ssid]["signal"]:
            seen[ssid] = entry
    return sorted(seen.values(), key=lambda e: -e["signal"])


def start_ap():
    _nmcli("connection", "delete", AP_CON)          # idempotent (échec ignoré)
    r = _nmcli("device", "wifi", "hotspot", "ifname", IFACE,
               "con-name", AP_CON, "ssid", AP_SSID)
    if r.returncode != 0:
        raise RuntimeError(f"hotspot: {r.stderr.strip()}")
    logger.info("AP « %s » actif (%s)", AP_SSID, AP_IP)


def stop_ap():
    _nmcli("connection", "down", AP_CON)
    _nmcli("connection", "delete", AP_CON)


def try_connect(ssid: str, password: str) -> bool:
    """Coupe l'AP, tente le réseau cible, attend l'Internet réel. Échec → purge
    le profil (sinon NetworkManager le retente en boucle à chaque boot)."""
    stop_ap()
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    r = _nmcli(*args, timeout=CONNECT_TIMEOUT_S)
    if r.returncode == 0:
        for _ in range(10):                          # DHCP + route + DNS : jusqu'à 20 s
            time.sleep(2)
            if has_connectivity():
                return True
    _nmcli("connection", "delete", ssid)
    return False


# ── serveur HTTP du portail ──────────────────────────────────────────
_PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aura — Configuration Wi-Fi</title><style>
body{{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#FAF5EF;color:#1F1B16;
margin:0;padding:24px;display:flex;justify-content:center}}
main{{max-width:420px;width:100%}}
h1{{color:#C2410C;font-size:26px;margin:18px 0 6px}}
p{{color:#6B5D4F;font-size:15px;line-height:1.5}}
.net{{display:flex;align-items:center;gap:10px;width:100%;padding:14px 16px;margin:8px 0;
border:1px solid #E7DDD1;border-radius:12px;background:#fff;font-size:16px;cursor:pointer}}
.net:active{{background:#FFF3EA}}
.sig{{margin-left:auto;color:#6B5D4F;font-size:13px}}
input{{width:100%;box-sizing:border-box;padding:14px;margin:8px 0;border:1px solid #E7DDD1;
border-radius:12px;font-size:16px;background:#fff}}
button{{width:100%;padding:15px;margin-top:10px;border:0;border-radius:12px;background:#C2410C;
color:#fff;font-size:17px;font-weight:600}}
.badge{{display:inline-block;background:#FFF3EA;color:#C2410C;border-radius:20px;
padding:4px 12px;font-size:13px;font-weight:600}}
</style></head><body><main>
<span class="badge">● Aura</span>
<h1>{title}</h1>
{body}
</main></body></html>"""


def _page_home(networks: list[dict]) -> str:
    items = "".join(
        f'<div class="net" onclick="pick(\'{html.escape(n["ssid"], quote=True)}\',{int(n["secured"])})">'
        f'{"🔒 " if n["secured"] else "🔓 "}{html.escape(n["ssid"])}'
        f'<span class="sig">{n["signal"]} %</span></div>'
        for n in networks) or "<p><i>Aucun réseau détecté — utilisez le champ manuel.</i></p>"
    body = f"""
<p>Choisissez le réseau Wi-Fi de votre entreprise :</p>{items}
<form method="post" action="/connect" id="f">
  <input name="ssid" id="ssid" placeholder="Nom du réseau (SSID)" required>
  <input name="password" id="pw" type="password" placeholder="Mot de passe (vide si réseau ouvert)">
  <button>Connecter Aura</button>
</form>
<script>function pick(s,sec){{document.getElementById('ssid').value=s;
document.getElementById('pw').focus();window.scrollTo(0,document.body.scrollHeight);}}</script>"""
    return _PAGE.format(title="Connectons votre enceinte", body=body)


def _page_bye(ssid: str) -> str:
    body = (f"<p>Aura se connecte à « <b>{html.escape(ssid)}</b> »…</p>"
            "<p><b>Le résultat vous sera annoncé à voix haute par l'enceinte.</b></p>"
            "<p>Si la connexion échoue, le réseau <b>Aura-Config</b> réapparaîtra "
            "dans une minute pour réessayer.</p>")
    return _PAGE.format(title="Connexion en cours…", body=body)


class _Portal(BaseHTTPRequestHandler):
    networks: list[dict] = []
    result: dict = {}

    def log_message(self, *a):                       # silencieux (journal systemd propre)
        pass

    def _send(self, code: int, content: str, ctype="text/html; charset=utf-8"):
        data = content.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        # Détection captive (Android /generate_204, iOS /hotspot-detect…) : toute
        # requête hors 10.42.0.1 est redirigée vers le portail → la page s'ouvre seule.
        if host != AP_IP:
            self.send_response(302)
            self.send_header("Location", f"http://{AP_IP}/")
            self.end_headers()
            return
        self._send(200, _page_home(self.networks))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        ssid = (form.get("ssid") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        if not ssid or not re.match(r"^[^\x00-\x1f]{1,32}$", ssid):
            self._send(400, _PAGE.format(title="Nom de réseau invalide",
                                         body="<p><a href='/'>Retour</a></p>"))
            return
        self._send(200, _page_bye(ssid))
        _Portal.result = {"ssid": ssid, "password": password}


def run_portal_once() -> bool:
    """Un cycle complet : scan → AP → attendre la saisie → tenter. Vrai si en ligne."""
    networks = scan_networks()
    logger.info("%d réseaux détectés", len(networks))
    _Portal.networks = networks
    _Portal.result = {}
    start_ap()
    say("portal_start")
    server = ThreadingHTTPServer(("0.0.0.0", 80), _Portal)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        last_prompt = time.time()
        while not _Portal.result:
            time.sleep(1)
            # câble Ethernet branché entre-temps → sortie immédiate
            if has_connectivity():
                say("portal_ok")
                return True
            if time.time() - last_prompt > 300:      # rappel vocal toutes les 5 min
                say("portal_start")
                last_prompt = time.time()
        creds = _Portal.result
    finally:
        server.shutdown()
        t.join(timeout=3)
    say("portal_connecting")
    if try_connect(creds["ssid"], creds["password"]):
        logger.info("connecté à « %s »", creds["ssid"])
        say("portal_ok")
        return True
    logger.info("échec de connexion à « %s »", creds["ssid"])
    say("portal_fail")
    return False


def main():
    logger.info("surveillance connectivité (grâce %d s, iface %s)", OFFLINE_GRACE_S, IFACE)
    offline_since: float | None = None
    while True:
        if has_connectivity():
            offline_since = None
            time.sleep(CHECK_EVERY_S)
            continue
        offline_since = offline_since or time.time()
        if time.time() - offline_since < OFFLINE_GRACE_S:
            time.sleep(5)
            continue
        logger.info("hors ligne depuis %d s → portail", int(time.time() - offline_since))
        try:
            if run_portal_once():
                offline_since = None
        except Exception as e:
            logger.error("portail: %s", e)
            stop_ap()                                 # jamais laisser un AP zombie
            time.sleep(15)


if __name__ == "__main__":
    main()

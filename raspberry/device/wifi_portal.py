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
        r = subprocess.run(cmd + [str(wav)], timeout=30, capture_output=True, text=True)
        if r.returncode != 0:                        # ne JAMAIS avaler un échec audio
            logger.warning("aplay %s (dev=%s) rc=%d : %s",
                           name, dev or "défaut", r.returncode, (r.stderr or "").strip()[:120])
    except Exception as e:
        logger.warning("aplay %s (dev=%s): %s", name, dev or "défaut", e)


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
    """AP OUVERT (sans mot de passe — c'est un portail d'accueil, pas un réseau).
    ⚠️ Ne PAS utiliser `nmcli device wifi hotspot` : sans mot de passe fourni,
    il en GÉNÈRE un aléatoire (terrain 27/07 : le téléphone en demandait un).
    La création explicite sans bloc de sécurité donne un vrai réseau ouvert."""
    _nmcli("connection", "delete", AP_CON)          # idempotent (échec ignoré)
    r = _nmcli("connection", "add", "type", "wifi", "ifname", IFACE,
               "con-name", AP_CON, "autoconnect", "no", "ssid", AP_SSID,
               "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
               "ipv4.method", "shared")
    if r.returncode != 0:
        raise RuntimeError(f"ap add: {r.stderr.strip()}")
    r = _nmcli("connection", "up", AP_CON, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ap up: {r.stderr.strip()}")
    logger.info("AP ouvert « %s » actif (%s)", AP_SSID, AP_IP)


def stop_ap():
    _nmcli("connection", "down", AP_CON)
    _nmcli("connection", "delete", AP_CON)


def _wait_device_free(timeout_s: int = 15) -> bool:
    """Attend que l'interface soit sortie du mode AP (état disconnected/available).
    Terrain 27/07 : après l'extinction de l'AP, la radio met plusieurs secondes à
    redevenir cliente — un connect lancé trop tôt échoue SYSTÉMATIQUEMENT (d'où
    le motif « 1re tentative échoue, 2e marche »). On attend l'ÉTAT, pas un délai."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _nmcli("-t", "-f", "DEVICE,STATE", "device", "status", timeout=10)
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if parts[0] == IFACE and len(parts) > 1:
                if parts[1] in ("disconnected", "connecting", "connected"):
                    return True
        time.sleep(0.5)
    return False


def _wait_ssid_visible(ssid: str, timeout_s: int = 25) -> bool:
    """Rescanne jusqu'à ce que le SSID cible apparaisse réellement dans le scan
    (le cache est vide/périmé au sortir du mode AP ; les premiers rescan peuvent
    être refusés par la radio — on insiste jusqu'à VOIR le réseau)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _nmcli("device", "wifi", "rescan", timeout=20)   # échec toléré (radio occupée)
        time.sleep(2.5)
        r = _nmcli("-t", "-f", "SSID", "device", "wifi", "list", timeout=20)
        if ssid in [l.strip() for l in r.stdout.splitlines()]:
            return True
    return False


def try_connect(ssid: str, password: str) -> bool:
    """Coupe l'AP, attend les ÉTATS réels (radio libre → SSID visible), se
    connecte, attend l'Internet réel. Échec → purge du profil (sinon
    NetworkManager le retente en boucle à chaque boot)."""
    stop_ap()
    if not _wait_device_free():
        logger.warning("l'interface %s tarde à quitter le mode AP", IFACE)
    visible = _wait_ssid_visible(ssid)
    if not visible:
        logger.info("« %s » invisible au scan → tentative en réseau masqué", ssid)
    last_err = ""
    for attempt in range(1, 3):
        args = ["device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        if not visible:
            args += ["hidden", "yes"]                # SSID masqué (ou hors de portée)
        r = _nmcli(*args, timeout=CONNECT_TIMEOUT_S)
        if r.returncode == 0:
            for _ in range(15):                      # DHCP + route + DNS : jusqu'à 30 s
                time.sleep(2)
                if has_connectivity():
                    logger.info("connecté à « %s » (essai %d)", ssid, attempt)
                    return True
            last_err = "associé mais pas d'Internet (captif entreprise ? DNS ?)"
        else:
            last_err = (r.stderr or r.stdout).strip()
        logger.warning("connect « %s » essai %d/2 : %s", ssid, attempt, last_err)
        low = last_err.lower()
        if "key-mgmt" in low and password:
            # Cache de scan encore vide → nmcli ne peut pas DEVINER le chiffrement
            # (terrain 27/07 : « 802-11-wireless-security.key-mgmt: property is
            # missing » ×3). Repli : profil EXPLICITE wpa-psk, zéro devinette.
            # wpa-psk d'abord (WPA2 + mixte WPA2/WPA3 = ~95 % du parc), puis
            # sae (WPA3 pur, routeurs récents stricts).
            for key_mgmt in ("wpa-psk", "sae"):
                logger.info("repli profil explicite %s pour « %s »", key_mgmt, ssid)
                _nmcli("connection", "delete", ssid)
                add = _nmcli("connection", "add", "type", "wifi", "ifname", IFACE,
                             "con-name", ssid, "ssid", ssid,
                             "wifi-sec.key-mgmt", key_mgmt, "wifi-sec.psk", password)
                if add.returncode != 0:
                    last_err = (add.stderr or add.stdout).strip()
                    continue
                up = _nmcli("connection", "up", ssid, timeout=CONNECT_TIMEOUT_S)
                if up.returncode == 0:
                    for _ in range(15):
                        time.sleep(2)
                        if has_connectivity():
                            logger.info("connecté à « %s » (profil explicite %s)",
                                        ssid, key_mgmt)
                            return True
                last_err = (up.stderr or up.stdout).strip()
                logger.warning("profil explicite %s « %s » : %s", key_mgmt, ssid, last_err)
        if "secrets" in low or "password" in low or "802.1x" in low:
            break                                    # mauvais mot de passe → définitif
        time.sleep(3)
    _nmcli("connection", "delete", ssid)
    return False


# ── serveur HTTP du portail ──────────────────────────────────────────
# Design system Aura (Phase 11) : orange #C2410C sur crème, polices système
# (AUCUNE ressource externe : le téléphone est sur un AP SANS Internet).
_PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Aura — Configuration Wi-Fi</title><style>
:root{--brand:#C2410C;--brand-soft:#FFF3EA;--ink:#1F1B16;--muted:#6B5D4F;
--line:#E7DDD1;--bg:#FAF5EF;--card:#FFFFFF;--ok:#1A7F4B}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:var(--bg);color:var(--ink);min-height:100vh;display:flex;flex-direction:column;
align-items:center;padding:20px 16px 32px;-webkit-font-smoothing:antialiased}
header{display:flex;align-items:center;gap:12px;width:100%;max-width:430px;padding:4px 2px 18px}
.orb{width:38px;height:38px;border-radius:50%;flex-shrink:0;
background:radial-gradient(circle at 32% 28%,#FFB27A 0%,#E36B2B 45%,var(--brand) 100%);
box-shadow:0 2px 10px rgba(194,65,12,.35)}
.brand{font-size:21px;font-weight:700;letter-spacing:-.02em}
.brand small{display:block;font-size:12px;font-weight:400;color:var(--muted);letter-spacing:0}
main{width:100%;max-width:430px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;
padding:22px 20px;box-shadow:0 1px 4px rgba(31,27,22,.05)}
h1{font-size:22px;letter-spacing:-.01em;margin-bottom:6px}
.sub{color:var(--muted);font-size:14.5px;line-height:1.5;margin-bottom:16px}
.nets{display:flex;flex-direction:column;gap:8px;margin-bottom:4px}
.net{display:flex;align-items:center;gap:12px;width:100%;padding:14px;text-align:left;
border:1.5px solid var(--line);border-radius:13px;background:var(--card);font-size:16px;
font-weight:500;cursor:pointer;transition:border-color .15s,background .15s}
.net.sel{border-color:var(--brand);background:var(--brand-soft)}
.net .lock{color:var(--muted);flex-shrink:0;display:flex}
.bars{margin-left:auto;display:flex;align-items:flex-end;gap:2px;height:16px;flex-shrink:0}
.bars i{width:4px;border-radius:2px;background:var(--line)}
.bars i:nth-child(1){height:5px}.bars i:nth-child(2){height:8px}
.bars i:nth-child(3){height:12px}.bars i:nth-child(4){height:16px}
.bars.s1 i:nth-child(-n+1),.bars.s2 i:nth-child(-n+2),
.bars.s3 i:nth-child(-n+3),.bars.s4 i{background:var(--ok)}
#panel{display:none;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
#panel.open{display:block}
label{display:block;font-size:13.5px;font-weight:600;color:var(--muted);margin:0 0 6px 2px}
.pwrow{position:relative}
input{width:100%;padding:15px 52px 15px 14px;border:1.5px solid var(--line);border-radius:13px;
font-size:17px;background:var(--card);color:var(--ink)}
input:focus{outline:none;border-color:var(--brand)}
.eye{position:absolute;right:6px;top:50%;transform:translateY(-50%);border:0;background:none;
padding:10px;color:var(--muted);cursor:pointer;font-size:13.5px;font-weight:600}
.hint{font-size:12.5px;color:var(--muted);margin:6px 2px 0}
.go{width:100%;padding:16px;margin-top:16px;border:0;border-radius:13px;background:var(--brand);
color:#fff;font-size:17px;font-weight:600;cursor:pointer}
.go:disabled{opacity:.55}
details{margin-top:14px}
summary{font-size:13.5px;color:var(--muted);cursor:pointer}
details input{margin-top:8px;padding-right:14px}
.steps{display:flex;flex-direction:column;gap:14px;margin-top:14px}
.step{display:flex;gap:12px;align-items:flex-start;font-size:15px;line-height:1.45}
.step .n{width:24px;height:24px;border-radius:50%;background:var(--brand-soft);color:var(--brand);
font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.spin{width:22px;height:22px;border:3px solid var(--brand-soft);border-top-color:var(--brand);
border-radius:50%;animation:r 1s linear infinite;flex-shrink:0}
@keyframes r{to{transform:rotate(360deg)}}
footer{margin-top:auto;padding-top:22px;font-size:12px;color:var(--muted)}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style></head><body>
<header><div class="orb"></div><div class="brand">Aura<small>Assistant vocal — Hallia</small></div></header>
<main><div class="card">__BODY__</div></main>
<footer>Aura par Hallia · configuration locale sécurisée</footer>
</body></html>"""

_LOCK_SVG = ('<svg class="lock" width="15" height="15" viewBox="0 0 24 24" fill="none" '
             'stroke="currentColor" stroke-width="2.2"><rect x="4" y="10" width="16" height="11" '
             'rx="2.5"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>')


def _bars(signal: int) -> str:
    level = 1 + min(3, signal // 25)                 # 0-100 % → 1-4 barres
    return f'<span class="bars s{level}"><i></i><i></i><i></i><i></i></span>'


def _page_home(networks: list[dict]) -> str:
    items = "".join(
        '<button type="button" class="net" '
        f'''onclick="pick(this,'{html.escape(n["ssid"], quote=True)}',{int(n["secured"])})">'''
        f'{_LOCK_SVG if n["secured"] else ""}'
        f'<span>{html.escape(n["ssid"])}</span>{_bars(n["signal"])}</button>'
        for n in networks) or ('<p class="sub">Aucun réseau détecté pour l\'instant — '
                               'utilisez « Réseau masqué » ci-dessous.</p>')
    body = f"""
<h1>Connecter votre enceinte</h1>
<p class="sub">Choisissez le réseau Wi-Fi de votre entreprise. Aura s'y connectera
et vous confirmera à voix haute.</p>
<form method="post" action="/connect" id="f">
  <div class="nets" id="nets">{items}</div>
  <input type="hidden" name="ssid" id="ssid">
  <div id="panel">
    <label id="pwlabel" for="pw">Mot de passe du réseau</label>
    <div class="pwrow">
      <input name="password" id="pw" type="password" autocomplete="off"
             autocapitalize="none" autocorrect="off" spellcheck="false"
             placeholder="Mot de passe">
      <button type="button" class="eye" id="eye" onclick="toggle()"
              aria-label="Afficher le mot de passe">Afficher</button>
    </div>
    <p class="hint">Vérifiez le mot de passe avec « Afficher » avant de valider —
    attention aux majuscules automatiques du téléphone.</p>
    <button class="go" id="go">Connecter Aura</button>
  </div>
  <details>
    <summary>Réseau masqué ou absent de la liste ?</summary>
    <input id="manual" placeholder="Nom exact du réseau (SSID)" autocapitalize="none"
           autocorrect="off" spellcheck="false" oninput="manualPick(this.value)">
  </details>
</form>
<script>
var sec=1, pick;
function toggle(){{
  var p=document.getElementById('pw'),e=document.getElementById('eye');
  var show=p.type==='password';p.type=show?'text':'password';
  e.textContent=show?'Masquer':'Afficher';
}}
pick=function(el,ssid,secured){{
  document.querySelectorAll('.net').forEach(function(n){{n.classList.remove('sel')}});
  if(el)el.classList.add('sel');
  document.getElementById('ssid').value=ssid;
  sec=secured;
  var panel=document.getElementById('panel');
  panel.classList.add('open');
  document.getElementById('pwlabel').textContent=
    secured?('Mot de passe de « '+ssid+' »'):'Réseau ouvert — aucun mot de passe requis';
  document.getElementById('pw').style.display=secured?'':'none';
  document.getElementById('eye').style.display=secured?'':'none';
  if(secured)document.getElementById('pw').focus();
  panel.scrollIntoView({{behavior:'smooth',block:'end'}});
}};
function manualPick(v){{
  document.querySelectorAll('.net').forEach(function(n){{n.classList.remove('sel')}});
  document.getElementById('ssid').value=v.trim();
  var panel=document.getElementById('panel');
  if(v.trim()){{panel.classList.add('open');
    document.getElementById('pwlabel').textContent='Mot de passe de « '+v.trim()+' » (vide si réseau ouvert)';
    document.getElementById('pw').style.display='';
    document.getElementById('eye').style.display='';
  }} else panel.classList.remove('open');
}}
document.getElementById('f').addEventListener('submit',function(ev){{
  if(!document.getElementById('ssid').value){{ev.preventDefault();return;}}
  var b=document.getElementById('go');b.disabled=true;b.textContent='Connexion en cours…';
}});
</script>"""
    return _PAGE.replace("__BODY__", body)


def _page_bye(ssid: str) -> str:
    body = f"""
<h1>Connexion en cours</h1>
<div class="steps">
  <div class="step"><div class="spin"></div>
    <div>Aura se connecte à «&nbsp;<b>{html.escape(ssid)}</b>&nbsp;»…</div></div>
  <div class="step"><div class="n">🔊</div>
    <div><b>Le résultat vous sera annoncé à voix haute</b> par l'enceinte
    dans quelques secondes.</div></div>
  <div class="step"><div class="n">↺</div>
    <div>En cas d'échec, le réseau <b>Aura-Config</b> réapparaîtra
    automatiquement pour réessayer.</div></div>
</div>"""
    return _PAGE.replace("__BODY__", body)


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
            self._send(400, _PAGE.replace(
                "__BODY__", "<h1>Nom de réseau invalide</h1>"
                            "<p class='sub'><a href='/'>← Retour à la liste</a></p>"))
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

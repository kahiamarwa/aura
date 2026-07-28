"""Test de sortie d'usine (chantier 6, docs/PLUG-AND-PLAY.md) — ~2 min à l'atelier.

    python -m device.tools.factory_test [--skip-network] [--no-operator] [--loops N]

Lancé sur l'enceinte assemblée AVANT mise en carton. Séquence, chaque étape
imprimant ✅/❌ + détail, verdict final OK/KO, code retour 0/1 :

  1. USB      : l'array ReSpeaker XVF3800 énumère (2886:001a) ;
  2. FIRMWARE : version lue via le binaire xvf_host ≥ 2.0.10 (lecture SEULE —
                VERSION est une commande sûre, cf. règle d'or du runbook) ;
  3. LED      : cycle LISTENING → THINKING → MUTED → IDLE via XvfLedRing,
                confirmé par l'opérateur (ou auto en --no-operator) ;
  4. AUDIO    : bip sinus 1 kHz joué sur le haut-parleur PENDANT une capture
                2 s du canal ASR → pic FFT à 1 kHz = HP **et** micro validés
                d'un coup (le test clé : toute la chaîne acoustique) ;
  5. RÉSEAU   : backend joignable (WARN si hors ligne — l'atelier peut ne pas
                avoir Internet, un backend en panne ne jette pas une enceinte).

Robustesse : CHAQUE étape est isolée dans son try/except — une exception fait
échouer l'ÉTAPE, jamais le script (le verdict doit toujours tomber à l'atelier).
"""

import os
import re
import sys
import time
import wave
import shutil
import argparse
import tempfile
import subprocess

import numpy as np

from .. import config
# Réutilise l'identité USB déclarée UNE fois dans xvf_led (source de vérité) —
# et la classe XvfLedRing elle-même : on ne réécrit pas le protocole vendor.
from ..xvf_led import XvfLedRing, _VID, _PID

# ── Constantes du test ───────────────────────────────────────────────
# Firmware minimal : 2.0.10 (corrige gel DoA sous LED custom + fiabilise
# l'array — cf. docs/hardware/respeaker-xvf3800.md §2). Comparaison de TUPLES :
# une future 2.0.11 ou 2.1.0 passe sans retoucher le test.
REQUIRED_FW = (2, 0, 10)

# Binaire C xvf_host (dépôt Seeed cloné à l'atelier, chemin standard de l'image
# golden). Surchargable par env si l'image bouge (XVF_HOST_BIN=/autre/chemin).
XVF_HOST_BIN = os.getenv(
    "XVF_HOST_BIN",
    "/home/pi/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host",
)

# Boucle acoustique : sinus 1 kHz (fréquence franche, loin du 50 Hz secteur et
# des ventilateurs, bien dans la bande voix du canal ASR), 0.8 s, capture 2 s
# (marge pour la latence de démarrage d'aplay ~0.1-0.3 s + queue du bip).
TONE_HZ = 1000.0
TONE_S = 0.8
REC_S = 2.0
TONE_BAND_HZ = 50.0            # pic cherché à 1 kHz ± 50 Hz (dérive/leakage)
# Seuil de détection : pic ≥ N × bruit médian du spectre. 8× par défaut —
# le gain numérique (AUDIO_INPUT_GAIN) multiplie signal ET bruit, le ratio est
# donc insensible au gain. Ajustable atelier (FACTORY_TONE_SNR=5 si ambiance
# bruyante, =15 pour durcir) SANS toucher au code.
TONE_SNR_MIN = float(os.getenv("FACTORY_TONE_SNR", "8"))

# Icônes de statut (SKIP = étape volontairement sautée, WARN = à noter mais
# n'invalide PAS l'enceinte — seuls les KO produisent un exit code 1).
_ICON = {"OK": "✅", "KO": "❌", "WARN": "⚠️ ", "SKIP": "⏭️ "}


def _run_step(title: str, fn, *args):
    """Exécute une étape en l'isolant : une exception = ❌ de l'étape, pas un
    crash du script — à l'atelier le verdict doit TOUJOURS tomber."""
    print(f"\n── {title}")
    try:
        status, detail = fn(*args)
    except Exception as e:                       # noqa: BLE001 — volontaire
        status, detail = "KO", f"exception inattendue : {e.__class__.__name__}: {e}"
    print(f"   {_ICON[status]} {detail}")
    return status, detail


# ── Étape 1 : présence USB de l'array ────────────────────────────────
def step_usb():
    # pyusb d'abord (même chemin que XvfLedRing) ; lsusb en secours si pyusb ou
    # le backend libusb manquent — le test d'usine doit marcher même sur une
    # image incomplète, quitte à le signaler.
    try:
        import usb.core
        if usb.core.find(idVendor=_VID, idProduct=_PID) is not None:
            return "OK", f"array XVF3800 détecté ({_VID:04x}:{_PID:04x}, pyusb)"
    except Exception:
        pass                                     # pyusb/libusb indispo → lsusb
    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True,
                             timeout=10).stdout.lower()
        if f"{_VID:04x}:{_PID:04x}" in out:
            return "OK", f"array XVF3800 détecté ({_VID:04x}:{_PID:04x}, lsusb)"
    except FileNotFoundError:
        return "KO", "ni pyusb ni lsusb disponibles — image golden incomplète ?"
    return "KO", (f"array {_VID:04x}:{_PID:04x} introuvable — câble USB-C ? "
                  "alimentation 5V/3A ? array HS ?")


# ── Étape 2 : version firmware via xvf_host ──────────────────────────
def _parse_version(text: str):
    """Extrait (major, minor, patch) de la sortie de `xvf_host VERSION`.

    Le binaire sort 3 uint8 (ex. « 2 0 10 », parfois précédés d'un libellé).
    On prend les 3 DERNIERS entiers de la dernière ligne qui en contient ≥ 3 :
    robuste aux préfixes du type « Device 0: VERSION 2 0 10 »."""
    for line in reversed([l for l in text.splitlines() if l.strip()]):
        nums = re.findall(r"\d+", line)
        if len(nums) >= 3:
            return tuple(int(n) for n in nums[-3:])
    return None


def step_firmware():
    bin_path = XVF_HOST_BIN
    if not os.path.isfile(bin_path):
        # Secours : un xvf_host dans le PATH (installations non standard).
        bin_path = shutil.which("xvf_host") or bin_path
    if not os.path.isfile(bin_path):
        return "KO", f"binaire xvf_host introuvable ({XVF_HOST_BIN}) — cloner le dépôt Seeed"
    try:
        r = subprocess.run([bin_path, "VERSION"], capture_output=True,
                           text=True, timeout=15)
    except subprocess.TimeoutExpired:
        # Un VERSION qui gèle = puce dans l'état warm-reboot cassé (bug #20) :
        # débrancher/rebrancher l'array physiquement puis relancer le test.
        return "KO", "xvf_host VERSION ne répond pas (bug warm-reboot #20 ? → replug USB)"
    out = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode != 0 and not out:
        return "KO", f"xvf_host a échoué (code {r.returncode}) — droits USB ? (règle udev 0666)"
    ver = _parse_version(out)
    if ver is None:
        return "KO", f"version illisible dans la sortie : {out[:120]!r}"
    ver_s = ".".join(map(str, ver))
    want_s = ".".join(map(str, REQUIRED_FW))
    if ver >= REQUIRED_FW:
        return "OK", f"firmware {ver_s} (requis ≥ {want_s})"
    return "KO", (f"firmware {ver_s} < {want_s} — flasher AVANT le carton "
                  "(DFU, cf. docs/hardware/respeaker-xvf3800-reference.md §3)")


# ── Étape 3 : anneau LED (cycle d'états) ─────────────────────────────
def step_led(no_operator: bool):
    # XvfLedRing ne lève jamais : échec d'init → available=False (message déjà
    # loggé par la classe : pyusb absent, array débranché, règle udev manquante…).
    led = XvfLedRing(enabled=True)
    if not led.available:
        return "KO", ("anneau LED indisponible — pyusb/libusb installés ? "
                      "règle udev MODE 0666 pour 2886:001a ? array branché ?")
    # 3 états visuellement TRÈS différents (couleur + mouvement) : un opérateur
    # tranche en un coup d'œil. 1.5 s chacun = le temps de voir le chenillard
    # tourner. Puis retour IDLE (état de repos du produit).
    print("   cycle : VERT+pointeur (écoute) → CHENILLARD ORANGE (réflexion) "
          "→ ROUGE FIXE (muet) → retour veille…")
    for state in ("LISTENING", "THINKING", "MUTED"):
        led.set_state(state)
        time.sleep(1.5)
    led.set_state("IDLE")
    time.sleep(1.0)                 # laisse le worker écrire l'état final
    # NB : pas de close() ici — l'atexit posé par la classe restaure l'effet
    # doa (défaut usine de la puce) à la sortie du script.
    if not led.available:
        # Le fail-safe interne (3 échecs d'écriture) s'est déclenché EN COURS
        # de cycle : l'USB est instable, l'enceinte n'est pas bonne.
        return "KO", "écritures LED en échec pendant le cycle (USB instable ?)"
    if no_operator:
        return "OK", "cycle exécuté sans erreur (available=True — non vérifié à l'œil)"
    try:
        ans = input("   → L'anneau a-t-il affiché VERT, puis CHENILLARD ORANGE, "
                    "puis ROUGE fixe ? [O/n] ")
    except EOFError:                # stdin non interactif → équivaut à --no-operator
        return "OK", "cycle exécuté sans erreur (stdin non interactif, non vérifié à l'œil)"
    if ans.strip().lower() in ("", "o", "oui", "y", "yes"):
        return "OK", "cycle confirmé par l'opérateur"
    return "KO", "anneau non conforme selon l'opérateur (LED HS ? nappe ?)"


# ── Étape 4 : boucle acoustique haut-parleur → micro (LE test clé) ───
def _make_tone_wav() -> str:
    """Génère le WAV du bip test : sinus TONE_HZ, mono 16 kHz, fade in/out
    (même recette anti-clic que audio_io._beep_wav)."""
    sr = config.SAMPLE_RATE
    n = int(sr * TONE_S)
    t = np.linspace(0, TONE_S, n, endpoint=False)
    env = np.minimum(1.0, np.minimum(t, TONE_S - t) * 40)     # fondu 25 ms
    data = (np.sin(2 * np.pi * TONE_HZ * t) * env * 0.6 * 32767).astype(np.int16)
    path = os.path.join(tempfile.gettempdir(), "aura_factory_tone_1khz.wav")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())
    return path


def _record_seconds(frames, seconds: float) -> np.ndarray:
    """Concatène `seconds` d'audio depuis le générateur MicStream.frames()
    (même recette que collect_wake_samples — dupliquée : 8 lignes, pas de
    dépendance d'un outil CLI vers un autre)."""
    chunks, need, got = [], int(seconds * config.SAMPLE_RATE), 0
    for frame in frames:
        chunks.append(frame)
        got += len(frame)
        if got >= need:
            break
    return np.concatenate(chunks)[:need] if chunks else np.zeros(0, dtype=np.int16)


def _tone_snr(rec: np.ndarray):
    """SNR du pic 1 kHz : max de la FFT dans TONE_HZ ± TONE_BAND_HZ, rapporté
    au bruit MÉDIAN du spectre 200-6000 Hz (bande 900-1100 Hz exclue pour ne
    pas compter la fuite spectrale du pic dans le « bruit »)."""
    if rec.size == 0:
        return 0.0, 0.0
    x = rec.astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(x ** 2)))
    mag = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, 1.0 / config.SAMPLE_RATE)
    band = (freqs >= TONE_HZ - TONE_BAND_HZ) & (freqs <= TONE_HZ + TONE_BAND_HZ)
    noise_mask = (freqs >= 200) & (freqs <= 6000) & ~((freqs >= 900) & (freqs <= 1100))
    peak = float(mag[band].max()) if band.any() else 0.0
    noise = float(np.median(mag[noise_mask])) if noise_mask.any() else 0.0
    # Micro mort (que des zéros) → peak=0, noise=0 → SNR 0 → KO, comme voulu.
    return peak / max(noise, 1e-12), rms


def step_acoustic(loops: int):
    # Imports TARDIFS : sounddevice/scipy peuvent manquer sur une image
    # incomplète — l'étape doit alors être ❌, pas le script entier.
    from ..audio_io import MicStream, _aplay_cmd
    wav_path = _make_tone_wav()
    print(f"   pipeline : device={config.INPUT_DEVICE!r} canaux={config.AUDIO_INPUT_CHANNELS} "
          f"canal={config.AUDIO_INPUT_CHANNEL} (ASR) gain=×{config.AUDIO_INPUT_GAIN:g} "
          f"sortie={config.PLAYBACK_ALSA_DEVICE or 'défaut ALSA'}")
    results = []
    # UN SEUL MicStream pour toutes les boucles : c'est le pipeline de capture
    # RÉEL d'Aura (extraction canal ASR + gain + resample éventuel) — on valide
    # exactement ce que verront wake word et STT en production.
    with MicStream() as mic:
        frames = mic.frames()
        _record_seconds(frames, 0.5)             # purge le transitoire d'ouverture
        for i in range(loops):
            # Lecture NON bloquante pendant que la capture tourne : le bip doit
            # être DANS la fenêtre d'enregistrement (0.8 s de bip, 2 s capturées,
            # marge pour la latence de démarrage d'aplay).
            try:
                proc = subprocess.Popen(_aplay_cmd(wav_path),
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                return "KO", "aplay introuvable — alsa-utils manquant sur l'image ?"
            rec = _record_seconds(frames, REC_S)
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            snr, rms = _tone_snr(rec)
            ok = snr >= TONE_SNR_MIN
            results.append((ok, snr))
            print(f"   boucle {i + 1}/{loops} : pic 1 kHz = {snr:.1f}× le bruit médian "
                  f"(seuil {TONE_SNR_MIN:g}×), rms={rms:.4f} → {'✅' if ok else '❌'}")
            if i + 1 < loops:
                time.sleep(0.4)                  # respiration entre les prises
    n_ok = sum(1 for ok, _ in results if ok)
    best = max(snr for _, snr in results)
    # Verdict : UNE détection nette suffit — elle prouve physiquement que le HP
    # émet ET que le micro capte (une prise ratée peut venir du bruit d'atelier,
    # pas du matériel). Les boucles supplémentaires (--loops) servent à lever un
    # doute, chaque prise étant affichée.
    if n_ok:
        return "OK", (f"boucle acoustique validée ({n_ok}/{loops} prises, "
                      f"meilleur pic {best:.1f}×) — HP + micro OK")
    return "KO", (f"pic 1 kHz jamais détecté (meilleur {best:.1f}× < {TONE_SNR_MIN:g}×) — "
                  "HP muet ? micro HS ? volume ALSA à zéro ? jack débranché ?")


# ── Étape 5 : réseau / backend ───────────────────────────────────────
def _http_status(url: str, timeout: float = 5.0) -> int:
    """Code HTTP d'un GET. requests si dispo (déjà une dépendance du device via
    cloud.py), urllib en secours. Les erreurs réseau remontent en exception."""
    try:
        import requests
    except ImportError:
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code                        # 4xx/5xx = réponse, pas une panne réseau
    return requests.get(url, timeout=timeout).status_code


def step_network(skip: bool):
    if skip:
        return "SKIP", "étape sautée (--skip-network)"
    # Même config que cloud.py : CLOUD_BACKEND_URL (device/config.py, chargée
    # depuis ~/.aura/env) — PAS « AURA_BACKEND_URL », qui n'existe pas.
    base = config.CLOUD_BACKEND_URL.rstrip("/")
    last_err = None
    for path in ("/health", "/"):                # /health (backend/app/routes/health.py), racine en secours
        url = base + path
        try:
            code = _http_status(url)
        except Exception as e:
            last_err = e
            continue
        if code < 500:
            return "OK", f"backend joignable : GET {url} → HTTP {code}"
        # 5xx = le RÉSEAU marche, c'est le service qui souffre : on ne jette
        # pas une enceinte saine pour un backend en panne → WARN, pas KO.
        return "WARN", f"backend répond HTTP {code} sur {url} (réseau OK, service en panne ?)"
    return "WARN", (f"backend injoignable ({base}) : {last_err} — atelier hors "
                    "ligne ? (n'invalide pas le matériel ; --skip-network pour masquer)")


# ── Orchestration + récapitulatif ────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-network", action="store_true",
                    help="saute l'étape réseau (atelier volontairement hors ligne)")
    ap.add_argument("--no-operator", action="store_true",
                    help="aucune question posée : le cycle LED est OK si aucune erreur USB")
    ap.add_argument("--loops", type=int, default=1, metavar="N",
                    help="répète N fois le test acoustique HP→micro (défaut 1)")
    args = ap.parse_args()
    loops = max(1, args.loops)

    print("═══ AURA — TEST DE SORTIE D'USINE ═══")
    print(f"backend={config.CLOUD_BACKEND_URL}  fw requis ≥ "
          + ".".join(map(str, REQUIRED_FW)))

    steps = [
        ("1/5 USB — array XVF3800", step_usb),
        ("2/5 FIRMWARE — xvf_host VERSION", step_firmware),
        ("3/5 LED — cycle d'états", step_led, args.no_operator),
        ("4/5 AUDIO — boucle HP → micro (1 kHz)", step_acoustic, loops),
        ("5/5 RÉSEAU — backend", step_network, args.skip_network),
    ]
    results = []
    for title, fn, *fn_args in steps:
        status, detail = _run_step(title, fn, *fn_args)
        results.append((title, status, detail))

    # Récap : tout sur une grille, lisible en un coup d'œil par l'opérateur.
    print("\n" + "─" * 66)
    print("RÉCAPITULATIF")
    for title, status, detail in results:
        print(f"  {_ICON[status]} {title:<38} {detail}")
    ko = [t for t, s, _ in results if s == "KO"]
    print("─" * 66)
    if ko:
        print(f"❌ VERDICT : KO — {len(ko)} étape(s) en échec → NE PAS mettre en carton")
        sys.exit(1)
    warned = any(s == "WARN" for _, s, _ in results)
    print("✅ VERDICT : OK — enceinte bonne pour le carton"
          + ("  (⚠️ avertissements ci-dessus à noter)" if warned else ""))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl+C opérateur : sortie propre, verdict NON rendu → code ≠ 0
        # (une enceinte au test interrompu n'est PAS validée).
        print("\n(test interrompu — enceinte NON validée)")
        sys.exit(130)

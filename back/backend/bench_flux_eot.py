#!/usr/bin/env python3
"""Benchmark des paramètres END-OF-TURN de Deepgram Flux (trouver les bonnes valeurs).

Rejoue des WAV (TES commandes réelles, AVEC tes hésitations/pauses) à travers Flux pour
plusieurs combos (eot_threshold, eot_timeout_ms) EN TEMPS RÉEL, et mesure pour chacun :
  • fragments : nb d'EndOfTurn pour 1 commande  → 1 = bon ; >1 = COUPÉ en plein milieu (le bug)
  • transcript final  → complet vs la référence (config très patiente = vérité terrain)
  • latence : délai entre la fin de l'audio et l'EndOfTurn → réactivité (plus bas = plus vif)
Puis recommande le meilleur combo : 0 coupure d'abord, latence minimale ensuite.

À lancer SUR LE SERVEUR (DEEPGRAM_API_KEY en env — JAMAIS sur le device). WAV 16k mono.
  DEEPGRAM_API_KEY=xxx python bench_flux_eot.py /chemin/vers/dossier_wavs/
  DEEPGRAM_API_KEY=xxx python bench_flux_eot.py cmd1.wav cmd2.wav cmd3.wav
"""
import asyncio
import glob
import json
import os
import sys
import time
import wave

import websockets

KEY = os.environ.get("DEEPGRAM_API_KEY")

# ── Grille à tester (édite-la librement) ────────────────────────────────
# Tu t'es fait couper → on explore surtout des valeurs PLUS PATIENTES (seuil ↑, timeout ↑).
THRESHOLDS = [0.6, 0.7, 0.8, 0.9]
TIMEOUTS_MS = [3000, 4000, 5000]
GRID = [{"th": t, "to": to} for t in THRESHOLDS for to in TIMEOUTS_MS]

# Référence "ultra patiente" → donne le transcript COMPLET attendu (vérité terrain).
REF = {"th": 0.9, "to": 8000}

WORD_MATCH_OK = 0.90        # un combo "passe" si ≥90% des mots de la référence sont là


def _url(th, to, eager=None):
    u = ("wss://api.deepgram.com/v2/listen?model=flux-general-multi&language_hint=fr"
         "&encoding=linear16&sample_rate=16000"
         f"&eot_threshold={th}&eot_timeout_ms={to}")
    if eager:
        u += f"&eager_eot_threshold={eager}"
    return u


def _load_pcm(path):
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            raise SystemExit(f"{path}: WAV 16k mono attendu")
        return w.readframes(w.getnframes())


async def _run(pcm, th, to):
    """Rejoue le WAV en temps réel ; renvoie (fragments, transcript, latence_s)."""
    hdr = {"Authorization": f"Token {KEY}"}
    try:
        ws = await websockets.connect(_url(th, to), additional_headers=hdr)
    except TypeError:
        ws = await websockets.connect(_url(th, to), extra_headers=hdr)

    turns, eot_times = [], []
    last_audio = [0.0]

    async def reader():
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") == "TurnInfo" and m.get("event") == "EndOfTurn":
                turns.append((m.get("transcript") or "").strip())
                eot_times.append(time.monotonic())

    rt = asyncio.create_task(reader())
    for i in range(0, len(pcm), 2560):        # 80 ms/chunk, EN TEMPS RÉEL
        await ws.send(pcm[i:i + 2560])
        last_audio[0] = time.monotonic()
        await asyncio.sleep(0.08)
    await asyncio.sleep(to / 1000 + 2.0)      # laisse Flux finir (timeout + marge)
    rt.cancel()
    try:
        await ws.close()
    except Exception:
        pass
    transcript = " ".join(t for t in turns if t)
    latency = (eot_times[-1] - last_audio[0]) if eot_times else None
    return len(turns), transcript, latency


def _completeness(got, ref):
    """Fraction des mots de la référence présents dans le transcript obtenu."""
    rw = ref.lower().split()
    if not rw:
        return 1.0
    gw = set(got.lower().split())
    return sum(1 for w in rw if w in gw) / len(rw)


async def main():
    if not KEY:
        raise SystemExit("DEEPGRAM_API_KEY manquant en env")
    args = sys.argv[1:]
    if not args:
        raise SystemExit("usage: python bench_flux_eot.py <dossier_wavs | fichier1.wav ...>")
    wavs = []
    for a in args:
        wavs += sorted(glob.glob(os.path.join(a, "*.wav"))) if os.path.isdir(a) else [a]
    if not wavs:
        raise SystemExit("aucun WAV trouvé")
    pcms = {w: _load_pcm(w) for w in wavs}

    # 1) Référence : transcript complet attendu par commande
    print(f"=== Référence (transcript complet attendu) — {len(wavs)} commande(s) ===")
    ref_txt = {}
    for w in wavs:
        _, txt, _ = await _run(pcms[w], REF["th"], REF["to"])
        ref_txt[w] = txt
        print(f"  {os.path.basename(w):28s} → {txt!r}")

    # 2) Grille
    print(f"\n=== Test de {len(GRID)} combos × {len(wavs)} commandes "
          f"(~{len(GRID) * len(wavs)} runs, patiente…) ===")
    results = []
    for cfg in GRID:
        passed, cuts, lats = 0, 0, []
        details = []
        for w in wavs:
            frags, txt, lat = await _run(pcms[w], cfg["th"], cfg["to"])
            comp = _completeness(txt, ref_txt[w])
            ok = (frags == 1 and comp >= WORD_MATCH_OK)
            passed += int(ok)
            cuts += max(0, frags - 1)
            if lat is not None:
                lats.append(lat)
            details.append((os.path.basename(w), frags, comp, lat))
        avg_lat = sum(lats) / len(lats) if lats else 99.0
        results.append({"cfg": cfg, "passed": passed, "cuts": cuts, "avg_lat": avg_lat, "details": details})
        flag = "✅" if passed == len(wavs) else ("⚠️ " if cuts else "  ")
        print(f"  {flag} seuil={cfg['th']} timeout={cfg['to']:>4}ms : "
              f"{passed}/{len(wavs)} OK | {cuts} coupure(s) | latence moy {avg_lat:.2f}s")

    # 3) Recommandation : d'abord 0 coupure (max OK), puis latence minimale
    best = sorted(results, key=lambda r: (-r["passed"], r["cuts"], r["avg_lat"]))[0]
    c = best["cfg"]
    print("\n=== 🏆 RECOMMANDATION ===")
    print(f"  eot_threshold = {c['th']}   eot_timeout_ms = {c['to']}")
    print(f"  → {best['passed']}/{len(wavs)} commandes sans coupure, "
          f"{best['cuts']} coupure(s), latence moyenne {best['avg_lat']:.2f}s")
    print("\n  À mettre dans backend/.env puis redémarrer le backend :")
    print(f"    FLUX_EOT_THRESHOLD={c['th']}")
    print(f"    FLUX_EOT_TIMEOUT_MS={c['to']}")

    # 4) Détail des coupures (pour comprendre OÙ ça coupe)
    bad = [r for r in results if r["cuts"]]
    if bad:
        print("\n=== Détail des combos qui coupent ===")
        for r in bad:
            for name, frags, comp, lat in r["details"]:
                if frags != 1:
                    print(f"  seuil={r['cfg']['th']} timeout={r['cfg']['to']} : "
                          f"{name} → {frags} fragments (coupé), {comp:.0%} des mots")


if __name__ == "__main__":
    asyncio.run(main())

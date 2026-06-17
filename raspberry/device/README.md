# Aura — Orchestrateur enceinte (device headless)

Un seul process Python qui transforme un Raspberry Pi en enceinte vocale Aura.
**Pas de navigateur, pas de front, aucune clé tierce sur le device.**

```
micro → wake word (ONNX local) → enregistre la commande
      → POST au cloud (STT + LLM + TTS) → joue le MP3
"Stop Aura" coupe la réponse en cours.
```

Le wake word est le seul traitement local always-on. Tout le reste (STT, LLM,
TTS, clés API) vit sur le backend cloud (`backend-aura.hallia.ai`).

## Prérequis système (Raspberry Pi OS)

```bash
sudo apt update
sudo apt install -y python3-venv libportaudio2 libsndfile1 mpg123
```

## Installation (testé sur Pi 5, Raspberry Pi OS, Python 3.13)

```bash
cd ~/aura/raspberry              # device/ vit ici, à côté de openwake/
python3 -m venv device/venv
source device/venv/bin/activate
python -m pip install --upgrade pip

# Deps (sans openwakeword pour l'instant)
python -m pip install -r device/requirements.txt

# openwakeword SANS tflite (Python 3.13 / ARM n'a pas de wheel tflite) :
python -m pip install openwakeword==0.6.0 --no-deps

# Modèles de pré-traitement openWakeWord (melspectrogram + embedding ONNX)
python -c "from openwakeword.utils import download_models; download_models()"
```

> **Gotchas Pi rencontrés** :
> - openwakeword tire `tflite-runtime` (pas de wheel 3.13/ARM) → `--no-deps` + onnxruntime.
> - openwakeword importe `sklearn` au chargement → `scikit-learn` est dans requirements.
> - Beaucoup de cartes USB (ex: SF-558) ne font pas 16 kHz → l'orchestrateur
>   capture en 48 kHz et sous-échantillonne (automatique, via scipy).

## Configuration (provisionnée à l'appairage, pas bakée en usine)

```bash
export CLOUD_BACKEND_URL=https://backend-aura.hallia.ai
export DEVICE_TOKEN=<token-du-device>          # doit matcher DEVICE_TOKEN du cloud
export USER_TOKEN=<JWT-utilisateur-appairé>    # Phase 2 : statique (voir note)
export AUDIO_INPUT_DEVICE=2                     # index micro (voir query_devices ci-dessous)
# Optionnel : ACTIVATE_MODEL, INTERRUPT_MODEL, CMD_SILENCE_RMS
```

Trouver l'index du micro :
```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
# la ligne avec '>' et des canaux d'entrée (in) = ton micro
```

> **Note Phase 2** : `USER_TOKEN` est un JWT statique le temps du prototype.
> En production (Phase 4), l'appairage QR fournit un token device→user révocable
> avec refresh — plus de JWT statique.

## Lancer

```bash
cd ~/aura/raspberry
source device/venv/bin/activate
python -m device.orchestrator
```

Dites **« Dis Aura »**, parlez votre commande, Aura répond. **« Stop Aura »** coupe.

## Côté cloud

L'endpoint `/api/device/converse` (dans `backend/`) fait STT→LLM→TTS.
Définir `DEVICE_TOKEN` dans le `.env` du backend cloud (même valeur que le device).

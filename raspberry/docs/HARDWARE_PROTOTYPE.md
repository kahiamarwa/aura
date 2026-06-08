# Aura - Prototype Hardware (Raspberry Pi)

## Liste d'achat

| # | Composant | Reference | Qte | Prix |
|---|---|---|---|---|
| 1 | Raspberry Pi 5 8GB | Raspberry Pi 5 Model B 8GB | 1 | 95 EUR |
| 2 | Alimentation officielle | USB-C 27W Pi 5 PSU | 1 | 15 EUR |
| 3 | microSD | Samsung EVO Plus 64GB A2 | 1 | 12 EUR |
| 4 | Ventilateur | Active Cooler officiel Pi 5 | 1 | 7 EUR |
| 5 | Micro array | ReSpeaker 2-Mic Pi HAT (Seeed Studio) | 1 | 12 EUR |
| 6 | Enceinte | Enceinte USB ou jack 3.5mm compacte | 1 | 15 EUR |
| 7 | Bouton mute | Bouton poussoir illumine rouge 16mm | 1 | 3 EUR |
| 8 | Fils | Kit Dupont M/F 40 pcs | 1 | 3 EUR |
| | | | **Total** | **~162 EUR** |

## Setup Raspberry Pi

### 1. OS
```bash
# Flasher Raspberry Pi OS Lite (64-bit) sur la microSD avec Raspberry Pi Imager
# Activer SSH + configurer WiFi dans les options avancees
```

### 2. Driver ReSpeaker 2-Mic
```bash
git clone https://github.com/respeaker/seeed-voicecard
cd seeed-voicecard
sudo ./install.sh
sudo reboot

# Verifier
arecord -D plughw:seeed2micvoicec -f S16_LE -r 16000 -c 1 test.wav
aplay test.wav
```

### 3. Audio sortie (jack 3.5mm ou USB)
```bash
# Lister les peripheriques
aplay -l

# Configurer la sortie par defaut dans /etc/asound.conf si necessaire
```

### 4. Bouton mute (GPIO)
```
Bouton mute → GPIO 17 + GND
LED rouge   → GPIO 22 + resistance 220 ohm + GND
```

### 5. LEDs ReSpeaker (3x RGB APA102)
Les LEDs du ReSpeaker 2-Mic HAT sont controlables via SPI :
- idle → bleu respiration lente
- listening → vert pulse rapide
- thinking → ambre rotation
- speaking → violet pulse
- conversing → cyan pulse doux
- mute → rouge fixe
- error → rouge clignotant

### 6. Daemon Aura
```bash
# Installer Python 3.11+
sudo apt install python3-pip python3-venv

# Cloner le projet
git clone <repo> /opt/aura
cd /opt/aura/backend

# Env virtuel + deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configurer .env
cp .env.example .env
# Editer avec les cles API

# Lancer
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 7. Service systemd (auto-start)
```ini
# /etc/systemd/system/aura.service
[Unit]
Description=Aura Voice Assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/aura/backend
Environment=PATH=/opt/aura/backend/.venv/bin
ExecStart=/opt/aura/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable aura
sudo systemctl start aura
```

## Mapping couleurs LED / etats

| Etat | Couleur | Hex | Animation |
|---|---|---|---|
| idle | Bleu | #3b82f6 | Respiration lente (4s) |
| listening | Vert | #22c55e | Pulse rapide (1s) |
| thinking | Ambre | #f59e0b | Rotation (chase) |
| speaking | Violet | #8b5cf6 | Pulse moyen |
| conversing | Cyan | #06b6d4 | Pulse doux (2s) |
| error | Rouge | #ef4444 | Clignotement rapide |
| MUTE | Rouge | fixe | Toujours allume |
| boot | Blanc | #ffffff | Remplissage progressif |

## Bouton MUTE - RGPD

Le bouton mute coupe physiquement l'alimentation du micro via un MOSFET sur le GPIO.
Meme si le logiciel est compromis, le micro est electrically deconnecte.

```
GPIO 17 (input, pull-up) ← Bouton poussoir
GPIO 22 (output)         → LED rouge (via 220 ohm)
GPIO 27 (output)         → Gate MOSFET (coupe 3.3V micro)
```

## Architecture logicielle sur le Pi

```
Raspberry Pi 5
├── aura-daemon (Python)
│   ├── Audio capture (ALSA, 16kHz mono)
│   ├── openWakeWord (ONNX, local)
│   ├── ElevenLabs STT (WebSocket)
│   ├── Backend API (FastAPI, local)
│   ├── ElevenLabs TTS → haut-parleur
│   ├── GPIO controller (boutons, LEDs)
│   └── BLE GATT server (app mobile)
├── Bluetooth 5.0 BLE
│   ├── Config WiFi (onboarding)
│   ├── Etat temps reel
│   ├── Volume
│   └── Auth (JWT Supabase)
└── WiFi 6
    └── Connexion backend cloud / Supabase
```

## Specs pour le fabricant chinois (futur)

Le prototype Pi sert de reference. Le fabricant remplacera :
- Pi 5 → SoC custom (Allwinner/Rockchip/MediaTek)
- ReSpeaker 2-Mic → Array 4 micros MEMS custom avec DSP (AEC, beamforming)
- Enceinte USB → Ampli + speaker integres
- Boitier prototype → Moule injection plastique
- Estimation cout unitaire (x1000) : 35-55 EUR/unite

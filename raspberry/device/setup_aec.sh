#!/usr/bin/env bash
# setup_aec.sh — Active l'annulation d'écho acoustique (WebRTC AEC) sur
# Raspberry Pi OS Bookworm (PipeWire). Le micro cesse de capter le haut-parleur
# → « Stop Aura » / barge-in fiables PENDANT qu'Aura parle.
#
# À lancer UNE FOIS, en tant que `pi` (PAS sudo — la partie systemd --user en a besoin) :
#   chmod +x setup_aec.sh && ./setup_aec.sh
# Puis activer côté app : export AEC_ENABLED=1  (dans ~/.aura/env ou device/.env)
#
# Architecture : module-echo-cancel crée une SOURCE virtuelle (mic nettoyé) et un
# SINK virtuel (sortie HP = référence d'écho). L'app capte la source et joue dans
# le sink ; le module corrèle les deux et soustrait l'écho. Alignement géré par l'OS.
set -euo pipefail

echo "==> [1/7] Détection du stack audio"
SERVER="$(pactl info 2>/dev/null | sed -n 's/^Server Name: //p' || true)"
if echo "$SERVER" | grep -qi "on PipeWire"; then
  echo "OK: PipeWire détecté ($SERVER)"
elif echo "$SERVER" | grep -qi "pulseaudio"; then
  echo "ATTENTION: PulseAudio classique (pas PipeWire). Ce script vise Bookworm/PipeWire."
  echo "  Fallback PulseAudio : pactl load-module module-echo-cancel aec_method=webrtc \\"
  echo "    source_name=echo-cancel-source sink_name=echo-cancel-sink ; puis set-default-*"
  exit 1
else
  echo "Aucun serveur audio détecté — on installe PipeWire ci-dessous."
fi

echo "==> [2/7] Installation des paquets requis"
sudo apt-get update
sudo apt-get install -y \
  pipewire pipewire-pulse pipewire-audio wireplumber \
  libspa-0.2-modules libasound2-plugins \
  pulseaudio-utils mpg123 alsa-utils
# libspa-0.2-modules => fournit aec/libspa-aec-webrtc (le WebRTC AEC)
# libasound2-plugins => fournit le PCM ALSA "pulse" (pour sounddevice/aplay/mpg123)
# pulseaudio-utils   => fournit `pactl` (utilisé par le service aec-default + vérifs)

echo "==> [3/7] Services PipeWire user + linger (survie au boot sans session)"
systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service || true
sudo loginctl enable-linger "$USER" || true

echo "==> [4/7] Conf echo-cancel PipeWire"
CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
mkdir -p "$CONF_DIR"
cat > "$CONF_DIR/99-echo-cancel.conf" <<'EOF'
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      library.name  = aec/libspa-aec-webrtc
      node.latency  = 1024/48000   # ~21 ms : bon compromis barge-in/qualité
      aec.args = {
        webrtc.gain_control      = true
        webrtc.noise_suppression = true
        webrtc.high_pass_filter  = true
        webrtc.extended_filter   = true
        webrtc.delay_agnostic    = true
        webrtc.voice_detection   = true
      }
      capture.props = {
        node.name   = "echo-cancel-capture"
        # target.object = "<node.name du vrai micro : pw-cli ls Node | grep node.name>"
      }
      source.props = {
        node.name        = "echo-cancel-source"
        node.description = "AEC Source (mic nettoye)"
      }
      sink.props = {
        node.name        = "echo-cancel-sink"
        node.description = "AEC Sink (sortie HP)"
      }
      playback.props = {
        node.name   = "echo-cancel-playback"
        # target.object = "<node.name du vrai HP>"
      }
    }
  }
]
EOF
echo "Conf écrite: $CONF_DIR/99-echo-cancel.conf"

echo "==> [5/7] PCM ALSA 'pulse' (pour sounddevice/aplay si non exposé)"
ASND="$HOME/.asoundrc"
if ! grep -q "type pulse" "$ASND" 2>/dev/null; then
cat >> "$ASND" <<'EOF'
pcm.pulse { type pulse }
ctl.pulse { type pulse }
pcm.aec   { type pulse  device "echo-cancel-source" }
EOF
echo "$ASND mis à jour."
fi

echo "==> [6/7] Redémarrage PipeWire pour charger le module"
systemctl --user restart wireplumber.service pipewire.service pipewire-pulse.service
sleep 3

echo "==> [7/7] Source/sink AEC par défaut + service de re-application au boot"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/aec-default.service" <<'EOF'
[Unit]
Description=Set AEC source/sink as PipeWire defaults
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service pipewire-pulse.service wireplumber.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for i in $(seq 1 30); do \
  pactl list short sources | grep -q echo-cancel-source && \
  pactl list short sinks   | grep -q echo-cancel-sink && break; sleep 1; done; \
  pactl set-default-source echo-cancel-source; \
  pactl set-default-sink   echo-cancel-sink'

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now aec-default.service

echo
echo "================= VÉRIFICATION ================="
pactl list short sources | grep echo-cancel || echo "!! source AEC absente"
pactl list short sinks   | grep echo-cancel || echo "!! sink AEC absent"
echo "Defaults:"; pactl info | grep -E "Default (Source|Sink)" || true
echo
echo "Test pratique (jouer un son + capter la source AEC en même temps) :"
echo "  aplay -D pulse /usr/share/sounds/alsa/Front_Center.wav &"
echo "  arecord -D pulse -f S16_LE -r 16000 -c1 -d 5 /tmp/aec_test.wav && aplay /tmp/aec_test.wav"
echo "  (l'écho du son joué doit être quasi absent de l'enregistrement)"
echo
echo "Côté app Aura : export AEC_ENABLED=1   (puis relancer l'orchestrateur)"
echo
echo "Si l'app tourne en SERVICE systemd : ce doit être un service --user, ou avec"
echo "  Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)  + loginctl enable-linger $USER"
echo "  (sinon elle ne verra pas le PipeWire de l'utilisateur → pas d'AEC)."
echo "FAIT."

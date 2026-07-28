#!/usr/bin/env bash
# Règle le volume de SORTIE de l'array ReSpeaker à 100 % (ou $1 %).
#
# Pourquoi un script dédié appelé par aura.service : le ExecStartPre
# « xvf_host REBOOT 1 » (contournement du bug warm-reboot #20) ré-énumère
# l'array à CHAQUE démarrage, APRÈS que le système ait restauré l'état ALSA
# (alsa-restore) → le volume revient au défaut bas. On le remet donc à 100 %
# ICI, après le REBOOT, à chaque lancement du service. « alsactl store » seul
# ne suffit pas pour cette raison (terrain 28/07).
set -u
CARD="${AUDIO_CARD:-Array}"
VOL="${1:-100}"

# Contrôles de sortie du XVF3800 USB — TOUS ceux présents sont réglés, aux
# deux index (terrain 28/07 : la carte expose PCM,0/PCM,1 + Headset,0/Headset,1).
found=0
for ctrl in "PCM" "Headset" "Speaker" "Master" "Headphone" "Playback"; do
  for idx in "" ",1"; do
    if amixer -c "$CARD" sget "${ctrl}${idx}" >/dev/null 2>&1; then
      amixer -c "$CARD" sset "${ctrl}${idx}" "${VOL}%" unmute >/dev/null 2>&1 \
        && echo "[volume] ${ctrl}${idx} → ${VOL}% (carte $CARD)" && found=1
    fi
  done
done
[ "$found" = 1 ] || echo "[volume] aucun contrôle de sortie connu sur $CARD (ignoré)" >&2
exit 0   # ne JAMAIS bloquer le démarrage d'Aura sur un réglage de volume

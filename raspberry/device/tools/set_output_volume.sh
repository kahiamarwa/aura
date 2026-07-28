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

# Contrôles de sortie candidats du XVF3800 USB — le 1er présent est réglé.
for ctrl in "PCM" "Speaker" "Master" "Headphone" "Playback"; do
  if amixer -c "$CARD" sget "$ctrl" >/dev/null 2>&1; then
    amixer -c "$CARD" sset "$ctrl" "${VOL}%" unmute >/dev/null 2>&1 \
      && echo "[volume] $ctrl → ${VOL}% (carte $CARD)" \
      && exit 0
  fi
done
echo "[volume] aucun contrôle de sortie connu sur la carte $CARD (ignoré)" >&2
exit 0   # ne JAMAIS bloquer le démarrage d'Aura sur un réglage de volume

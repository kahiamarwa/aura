#!/usr/bin/env bash
# =============================================================================
# install.sh — met en place TOUS les services Aura sur un Raspberry Pi
# (chantiers 3-5 du plug-and-play). Idempotent : relançable sans dégât.
#
#   sudo bash ~/aura/raspberry/device/tools/install.sh
#
# Deux usages :
#   • Enceinte EXISTANTE (déjà un DEVICE_TOKEN) : préserve l'identité, installe
#     juste les services. firstboot devient no-op (marqueur posé).
#   • Pi NEUF (image fraîche, pas d'identité) : firstboot générera DEVICE_TOKEN
#     + serial + register au premier démarrage du service.
#
# Ce que ça fait, et RIEN d'autre :
#   - copie les units systemd, la règle udev, le sudoers, la conf dnsmasq captif
#   - ajoute l'utilisateur pi aux groupes nécessaires (systemd-journal, plugdev)
#   - daemon-reload + enable des services (démarrage auto au boot)
# Ce que ça NE fait PAS : flasher le firmware, créer l'image, toucher au réseau.
# =============================================================================
set -euo pipefail

REPO="/home/pi/aura/raspberry"
SYSD="$REPO/device/systemd"
USER_NAME="pi"
ENV_FILE="/home/pi/.aura/env"
IDENTITY_MARKER="/home/pi/.aura/.identity_done"

log() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Lance avec sudo."; exit 1; }
[ -d "$SYSD" ] || { echo "Dépôt introuvable à $REPO — git pull d'abord ?"; exit 1; }

# ── 1. Groupes (accès journal pour send_logs, USB array pour les LED) ────────
log "Groupes de l'utilisateur $USER_NAME"
usermod -aG systemd-journal "$USER_NAME" 2>/dev/null || true
usermod -aG plugdev "$USER_NAME" 2>/dev/null || true
usermod -aG audio "$USER_NAME" 2>/dev/null || true
ok "systemd-journal, plugdev, audio"

# ── 2. Règle udev (accès sans sudo à l'array XVF3800) ────────────────────────
log "Règle udev de l'array ReSpeaker"
cp "$SYSD/99-aura-alsa.rules" /etc/udev/rules.d/
udevadm control --reload-rules && udevadm trigger || warn "udev trigger a bronché (sans gravité au prochain boot)"
ok "99-aura-alsa.rules"

# ── 3. sudoers ciblé (l'ordre distant « update » et rien d'autre) ────────────
log "sudoers minimal (systemctl start aura-update uniquement)"
install -m 440 "$SYSD/aura-sudoers" /etc/sudoers.d/aura
visudo -cf /etc/sudoers.d/aura >/dev/null && ok "sudoers valide" || { echo "sudoers invalide — retiré"; rm -f /etc/sudoers.d/aura; exit 1; }

# ── 4. DNS captif du portail Wi-Fi (page auto sur le téléphone) ──────────────
log "Conf dnsmasq du portail captif"
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cp "$SYSD/../systemd/aura-captive-dns.conf" /etc/NetworkManager/dnsmasq-shared.d/ 2>/dev/null \
  || cp "$SYSD/aura-captive-dns.conf" /etc/NetworkManager/dnsmasq-shared.d/
ok "aura-captive-dns.conf"

# ── 5. Identité : préserver une enceinte DÉJÀ configurée ─────────────────────
if grep -q '^DEVICE_TOKEN=' "$ENV_FILE" 2>/dev/null && [ ! -f "$IDENTITY_MARKER" ]; then
  warn "DEVICE_TOKEN déjà présent → enceinte existante : je pose le marqueur"
  warn "d'identité pour que firstboot ne génère PAS un nouveau token."
  SER="$(grep '^AURA_SERIAL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
  echo "${SER:-AUR-EXIST}" > "$IDENTITY_MARKER"
  chown "$USER_NAME:$USER_NAME" "$IDENTITY_MARKER"
  ok "identité existante préservée (marqueur = ${SER:-AUR-EXIST})"
else
  log "Pas d'identité → firstboot la générera au premier démarrage"
fi

# ── 6. Units systemd ─────────────────────────────────────────────────────────
log "Installation des services systemd"
for unit in aura.service aura-firstboot.service aura-update.service \
            aura-update.timer aura-wifi-portal.service; do
  cp "$SYSD/$unit" /etc/systemd/system/
done
systemctl daemon-reload
# enable SANS --now : on ne démarre pas aura tout de suite (l'orchestrateur
# tourne peut-être déjà à la main). Le vrai démarrage se fait au prochain boot,
# ou manuellement après avoir arrêté la session interactive.
systemctl enable aura.service aura-firstboot.service aura-update.timer \
                 aura-wifi-portal.service >/dev/null 2>&1
ok "services activés (démarrage auto au boot)"

echo
ok "Installation terminée."
echo
echo "Prochaines étapes :"
echo "  1. Si l'orchestrateur tourne à la main : Ctrl+C, puis"
echo "       sudo systemctl start aura"
echo "  2. Vérifier :  systemctl status aura   (→ active/running)"
echo "  3. Journal en direct :  journalctl -u aura -f"
echo "  4. Test de sortie d'usine (optionnel) :"
echo "       cd $REPO && device/venv/bin/python -m device.tools.factory_test"
echo
echo "Au REBOOT, l'enceinte démarre TOUTE SEULE (plus besoin de lancer python)."

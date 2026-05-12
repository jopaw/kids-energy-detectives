#!/usr/bin/env bash
# Energy Detectives — Wi-Fi hotspot setup for Raspberry Pi Zero 2 W
#
# Turns the Pi into a self-contained access point named "EnergyDetectives"
# so any device that joins it goes straight to the kids' dashboard — no
# router, no internet, no IP-address typing needed.
#
# What this script does:
#   1. Creates a NetworkManager Wi-Fi hotspot (SSID + WPA2 password).
#   2. Tells the hotspot's built-in DNS to answer every name with the Pi's
#      own IP, so phones / tablets pop up the dashboard automatically
#      (captive-portal style).
#   3. Downloads React + Babel into pi/vendor/ so the page works fully
#      offline (run this step while the Pi still has internet!).
#   4. Installs and starts the energy-detective systemd service on port 80.
#   5. Enables avahi-daemon so http://energy.local also resolves.
#
# Run on the Pi with:
#     sudo bash pi/setup-hotspot.sh
#
# To undo:
#     sudo bash pi/setup-hotspot.sh --uninstall
#
# Customisation: edit SSID / PASSWORD / AP_IP below before running.

set -euo pipefail

SSID="${SSID:-EnergyDetectives}"
PASSWORD="${PASSWORD:-lightning}"
CON_NAME="EnergyHotspot"
WIFI_IFACE="${WIFI_IFACE:-wlan0}"
AP_IP="${AP_IP:-10.42.0.1}"
SERVICE_NAME="energy-detective.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="${SUDO_USER:-${USER:-pi}}"

c_reset=$'\e[0m'; c_bold=$'\e[1m'; c_green=$'\e[32m'; c_yellow=$'\e[33m'; c_red=$'\e[31m'; c_cyan=$'\e[36m'

say()  { echo "${c_cyan}==>${c_reset} $*"; }
ok()   { echo "${c_green}✓${c_reset} $*"; }
warn() { echo "${c_yellow}!${c_reset} $*"; }
die()  { echo "${c_red}✗${c_reset} $*" >&2; exit 1; }

need_root() {
    [[ $EUID -eq 0 ]] || die "Please run with sudo: sudo bash $0"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "$2"
}

do_uninstall() {
    need_root
    say "Removing Energy Detectives hotspot setup"
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE_NAME"
    rm -rf "/etc/systemd/system/${SERVICE_NAME}.d"
    rm -f /etc/NetworkManager/dnsmasq-shared.d/energy-detective.conf
    nmcli con delete "$CON_NAME" 2>/dev/null || true
    systemctl daemon-reload
    ok "Uninstalled. The Pi will join its normal Wi-Fi again on reboot."
    exit 0
}

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" ]]; then
    do_uninstall
fi

need_root
require_cmd nmcli "NetworkManager (nmcli) is required. On Pi OS Bookworm it's preinstalled; on older releases:  sudo apt install network-manager"
require_cmd systemctl "systemctl is required (systemd-based Linux only)."

echo
echo "${c_bold}⚡ Energy Detectives — hotspot installer${c_reset}"
echo
echo "About to set up:"
echo "  SSID:        $SSID"
echo "  Password:    $PASSWORD"
echo "  Pi address:  $AP_IP"
echo "  Wi-Fi iface: $WIFI_IFACE"
echo "  Run as user: $RUN_USER"
echo "  Project at:  $PROJECT_DIR"
echo
if [[ "${1:-}" != "-y" && "${1:-}" != "--yes" ]]; then
    read -r -p "Continue? This will disconnect any existing Wi-Fi. [y/N] " ans
    case "${ans,,}" in y|yes) ;; *) die "Cancelled." ;; esac
fi

# --- 1. Vendor JS for offline page ---
VENDOR_DIR="$SCRIPT_DIR/vendor"
say "Downloading React + Babel into $VENDOR_DIR (needs internet right now)"
mkdir -p "$VENDOR_DIR"
declare -A vendor=(
    [react.production.min.js]="https://unpkg.com/react@18.3.1/umd/react.production.min.js"
    [react-dom.production.min.js]="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"
    [babel.min.js]="https://unpkg.com/@babel/standalone@7.24.7/babel.min.js"
)
for f in "${!vendor[@]}"; do
    if [[ -s "$VENDOR_DIR/$f" ]]; then
        ok "already have $f"
    else
        if curl -fsSL --connect-timeout 8 -o "$VENDOR_DIR/$f" "${vendor[$f]}"; then
            ok "downloaded $f"
        else
            warn "could not download $f — the page will fall back to the CDN if/when the Pi has internet."
        fi
    fi
done
chown -R "$RUN_USER:$RUN_USER" "$VENDOR_DIR"

# --- 2. Hotspot connection ---
say "Creating Wi-Fi hotspot profile ($CON_NAME)"
nmcli con delete "$CON_NAME" >/dev/null 2>&1 || true
nmcli con add type wifi ifname "$WIFI_IFACE" con-name "$CON_NAME" autoconnect yes ssid "$SSID" >/dev/null
nmcli con modify "$CON_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv4.addresses "$AP_IP/24" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASSWORD"
ok "Hotspot profile ready"

# --- 3. DNS catch-all for captive-portal style auto-popup ---
say "Configuring DNS catch-all (any URL → Pi)"
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat >/etc/NetworkManager/dnsmasq-shared.d/energy-detective.conf <<EOF
# Auto-installed by setup-hotspot.sh
# Resolve every DNS query on the hotspot to the Pi itself, so any URL
# typed on a connected device lands on the dashboard.
address=/#/$AP_IP
no-resolv
EOF
ok "DNS catch-all installed"

# --- 4. systemd service ---
say "Installing $SERVICE_NAME (port 80)"
install -m 644 /dev/null "/etc/systemd/system/$SERVICE_NAME"
cat >"/etc/systemd/system/$SERVICE_NAME" <<EOF
[Unit]
Description=Energy Detectives kids energy demo dashboard
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/energy_detective.py --port 80 --ap-ssid "$SSID" --ap-password "$PASSWORD" --ap-ip "$AP_IP"
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
ok "Service installed and enabled"

# --- 5. mDNS so http://energy.local works ---
say "Enabling avahi-daemon for http://energy.local"
apt-get install -y avahi-daemon >/dev/null 2>&1 || warn "could not install avahi-daemon (will try existing one)"
hostnamectl set-hostname energy 2>/dev/null || true
systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi-daemon not available — energy.local may not resolve"
ok "mDNS set up — Pi hostname is now 'energy'"

# --- 6. Bring the hotspot up ---
say "Activating hotspot (your SSH session may drop if you came in over Wi-Fi)"
nmcli con up "$CON_NAME" >/dev/null 2>&1 || warn "could not bring up hotspot yet — it will start on next boot"

# --- 7. Start the server ---
say "Starting $SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 1
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Server is running"
else
    warn "Server didn't start cleanly. Check: sudo journalctl -u $SERVICE_NAME -n 50"
fi

echo
echo "${c_green}${c_bold}✨ All set! Tell the kids:${c_reset}"
echo
echo "  📶  Connect to Wi-Fi:  ${c_bold}$SSID${c_reset}"
echo "  🔑  Password:          ${c_bold}$PASSWORD${c_reset}"
echo "  🌐  Then open:         ${c_bold}http://energy.local${c_reset}  (or http://$AP_IP)"
echo
echo "On most phones the page will pop up automatically when they connect."
echo
echo "Useful commands:"
echo "  sudo systemctl status  $SERVICE_NAME   # is it running?"
echo "  sudo journalctl -fu    $SERVICE_NAME   # follow logs"
echo "  sudo bash $0 --uninstall              # undo everything"
echo

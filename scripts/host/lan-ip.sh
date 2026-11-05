#!/bin/sh
# Publishes this Mac's current LAN IP for Homepage's "Network" card.
#
# Why this runs on the host: Docker Desktop NATs containers (CLAUDE.md house
# rule 6), so nothing inside the stack can see the Mac's real LAN address.
# This script writes it to ${DATA_ROOT}/config/host/host.json, which the
# lifecycle container serves at /host for the card to read. (Homepage itself
# only serves files present at its startup, so it can't serve this directly.)
# A launchd agent re-runs it on every network change and every 5 minutes,
# since the Mac's DHCP address has changed before.
#
# macOS only (uses route/ipconfig/launchd).
#
# Usage:
#   scripts/host/lan-ip.sh run        # write the file once
#   scripts/host/lan-ip.sh install    # install + start the launchd agent
#   scripts/host/lan-ip.sh uninstall  # stop + remove the launchd agent
set -eu

LABEL="com.neoflix.lan-ip"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

data_root() {
  sed -n 's/^DATA_ROOT=//p' "$REPO_ROOT/.env" | tr -d '"'"'"
}

write_ip() {
  out_dir="$(data_root)/config/host"
  mkdir -p "$out_dir"
  iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
  ip=""
  [ -n "$iface" ] && ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
  [ -n "$ip" ] || ip="offline"

  # Write-then-rename so lifecycle never serves a half-written file.
  tmp="$out_dir/.host.json.tmp"
  printf '{"lan_ip":"%s","interface":"%s"}\n' "$ip" "${iface:-none}" > "$tmp"
  mv "$tmp" "$out_dir/host.json"
}

install_agent() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>$SCRIPT_PATH</string>
    <string>run</string>
  </array>
  <!-- Fires when macOS network configuration changes (new DHCP lease, Wi-Fi switch). -->
  <key>WatchPaths</key>
  <array><string>/Library/Preferences/SystemConfiguration</string></array>
  <!-- Safety net in case a change doesn't touch the watched path. -->
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Installed $LABEL ($PLIST)"
}

uninstall_agent() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
}

case "${1:-}" in
  run) write_ip ;;
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  *) echo "usage: $0 run|install|uninstall" >&2; exit 2 ;;
esac

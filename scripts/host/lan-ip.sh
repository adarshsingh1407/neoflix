#!/bin/sh
# Publishes this Mac's current LAN and public IP to
# ${DATA_ROOT}/config/host/host.json, which the host dashboard
# (scripts/host/dashboard.sh, served from that same folder) reads. The
# optional lifecycle container also serves the file at /host.
#
# Why this runs on the host: the container VM NATs containers (CLAUDE.md house
# rule 6), so nothing inside the stack can see the Mac's real LAN address.
# A launchd agent re-runs it on every network change and every 5 minutes,
# since the Mac's DHCP address has changed before. The public IP comes from
# api.ipify.org (that service sees the request; nothing else is sent).
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

  # Public IP: only accept something shaped like an IPv4 address. On failure
  # keep the last known value, so a brief outage doesn't blank the dashboard.
  public_ip="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || true)"
  if ! printf '%s' "$public_ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
    public_ip="$(sed -n 's/.*"public_ip":"\([^"]*\)".*/\1/p' "$out_dir/host.json" 2>/dev/null || true)"
    [ -n "$public_ip" ] || public_ip="unknown"
  fi
  updated="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # Write-then-rename so a reader never sees a half-written file.
  tmp="$out_dir/.host.json.tmp"
  printf '{"lan_ip":"%s","interface":"%s","public_ip":"%s","updated":"%s"}\n' \
    "$ip" "${iface:-none}" "$public_ip" "$updated" > "$tmp"
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

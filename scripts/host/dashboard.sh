#!/bin/sh
# Serves the neoflix dashboard (scripts/host/dashboard/index.html) from the
# Mac itself on port 3000, outside Docker.
#
# Why on the host: the page shows the Mac's LAN IP, which only a host
# process can see (Docker Desktop NAT, CLAUDE.md house rule 6), and
# scripts/host/lan-ip.sh already runs here to write it. Serving a static page
# with macOS's bundled Python costs ~15 MB and no idle CPU, against ~170 MB
# for Homepage + Ofelia + Lifecycle in the Docker VM (DESIGN_DECISIONS.md
# decision #16/#17).
#
# What gets served: ONLY ${DATA_ROOT}/config/host (index.html copied in by
# this script, host.json written by lan-ip.sh). Never point it at config/ or
# DATA_ROOT itself: other apps' config files there hold API keys. Python's
# http.server is not hardened: keep it LAN-only, never port-forward it.
#
# macOS only (launchd). Needs the lan-ip agent too: `lan-ip.sh install`.
#
# Usage:
#   scripts/host/dashboard.sh install    # copy page + install/start server agent
#   scripts/host/dashboard.sh update     # re-copy the page after editing it
#   scripts/host/dashboard.sh uninstall  # stop + remove the server agent
set -eu

LABEL="com.neoflix.dashboard"
PORT=3000
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

serve_dir() {
  root="$(sed -n 's/^DATA_ROOT=//p' "$REPO_ROOT/.env" | tr -d '"'"'")"
  [ -n "$root" ] || { echo "DATA_ROOT not set in $REPO_ROOT/.env" >&2; exit 1; }
  echo "$root/config/host"
}

copy_page() {
  dir="$(serve_dir)"
  mkdir -p "$dir"
  cp "$HERE/dashboard/index.html" "$dir/index.html"
  echo "Copied page to $dir/index.html"
}

install_agent() {
  copy_page
  dir="$(serve_dir)"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string>
    <string>http.server</string>
    <string>$PORT</string>
    <string>--bind</string>
    <string>0.0.0.0</string>
    <string>--directory</string>
    <string>$dir</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- http.server logs every request to stderr; nothing worth keeping. -->
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Installed $LABEL: http://localhost:$PORT"
}

uninstall_agent() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
}

case "${1:-}" in
  install) install_agent ;;
  update) copy_page ;;
  uninstall) uninstall_agent ;;
  *) echo "usage: $0 install|update|uninstall" >&2; exit 2 ;;
esac

#!/bin/sh
# Serves the neoflix dashboard (scripts/host/dashboard/index.html) from the
# Mac itself on port 3000, outside Docker.
#
# Why on the host: the page shows the Mac's LAN IP, which only a host
# process can see (Docker Desktop NAT, CLAUDE.md house rule 6), and
# scripts/host/lan-ip.sh already runs here to write it. Serving a static page
# with macOS's bundled Python (dashboard/serve.py: stdlib http.server with a
# bigger connection backlog and cache headers) costs ~15 MB and no idle CPU, against ~170 MB
# for Homepage + Ofelia + Lifecycle in the Docker VM (DESIGN_DECISIONS.md
# decision #16/#17).
#
# What gets served: ONLY ${DATA_ROOT}/config/host — index.html + icons/
# (copied in by this script), host.json (written by lan-ip.sh) and media/
# (cached Jellyfin artwork written by dashboard/refresh_media.py; no API key).
# Never point it at config/ or DATA_ROOT itself: other apps' config files
# there hold API keys. Python's http.server is not hardened: keep it
# LAN-only, never port-forward it.
#
# macOS only (launchd). Needs the lan-ip agent too: `lan-ip.sh install`.
#
# Usage:
#   scripts/host/dashboard.sh install    # copy page, install server + artwork agents
#   scripts/host/dashboard.sh update     # re-copy page/icons after editing them
#   scripts/host/dashboard.sh refresh    # re-cache movie artwork from Jellyfin now
#   scripts/host/dashboard.sh uninstall  # stop + remove both agents
set -eu

LABEL="com.neoflix.dashboard"
MEDIA_LABEL="com.neoflix.dashboard-media"
PORT=3000
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
MEDIA_PLIST="$HOME/Library/LaunchAgents/$MEDIA_LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

serve_dir() {
  root="$(sed -n 's/^DATA_ROOT=//p' "$REPO_ROOT/.env" | tr -d '"'"'")"
  [ -n "$root" ] || { echo "DATA_ROOT not set in $REPO_ROOT/.env" >&2; exit 1; }
  echo "$root/config/host"
}

copy_page() {
  dir="$(serve_dir)"
  mkdir -p "$dir/icons"
  cp "$HERE/dashboard/index.html" "$dir/index.html"
  cp "$HERE"/dashboard/icons/* "$dir/icons/"
  echo "Copied page + icons to $dir"
}

refresh_media() {
  /usr/bin/python3 "$HERE/dashboard/refresh_media.py" "$(serve_dir)"
}

# write_plist <label> <plist-path> <extra plist keys> <program args...>
write_plist() {
  label="$1"; path="$2"; extra="$3"; shift 3
  args=""
  for a in "$@"; do args="$args    <string>$a</string>
"; done
  cat > "$path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>RunAtLoad</key><true/>
$extra
  <!-- Request/progress logs aren't worth keeping. -->
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$path"
}

install_agents() {
  copy_page
  dir="$(serve_dir)"
  mkdir -p "$HOME/Library/LaunchAgents"

  write_plist "$LABEL" "$PLIST" "  <key>KeepAlive</key><true/>" \
    /usr/bin/python3 "$HERE/dashboard/serve.py" "$PORT" "$dir"
  echo "Installed $LABEL: http://localhost:$PORT"

  # Artwork refresher: at load and every 6h. Cheap when nothing changed
  # (existing files are skipped), so no smarter trigger is needed.
  write_plist "$MEDIA_LABEL" "$MEDIA_PLIST" "  <key>StartInterval</key><integer>21600</integer>" \
    /bin/sh "$HERE/dashboard.sh" refresh
  echo "Installed $MEDIA_LABEL (artwork refresh every 6h)"
}

uninstall_agents() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$MEDIA_LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$MEDIA_PLIST"
  echo "Removed $LABEL and $MEDIA_LABEL"
}

case "${1:-}" in
  install) install_agents ;;
  update) copy_page ;;
  refresh) refresh_media ;;
  uninstall) uninstall_agents ;;
  *) echo "usage: $0 install|update|refresh|uninstall" >&2; exit 2 ;;
esac

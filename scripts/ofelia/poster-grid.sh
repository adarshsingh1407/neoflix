#!/bin/sh
set -eu

apk add --no-cache python3 py3-pillow py3-requests font-dejavu >/dev/null 2>&1

python3 /scripts/poster-grid.py

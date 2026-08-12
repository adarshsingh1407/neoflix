#!/bin/sh
set -eu

: "${JELLYFIN_URL:?JELLYFIN_URL not set}"
: "${JELLYFIN_API_KEY:?JELLYFIN_API_KEY not set}"
OUTPUT="/images/background.jpg"

apk add --no-cache curl jq >/dev/null 2>&1

IDS=$(curl -sf "${JELLYFIN_URL}/Items?Recursive=true&IncludeItemTypes=Movie,Series&Fields=BackdropImageTags" \
  -H "X-Emby-Token: ${JELLYFIN_API_KEY}" \
  | jq -r '[.Items[] | select(.BackdropImageTags | length > 0) | .Id] | .[]')

COUNT=$(printf '%s\n' "$IDS" | wc -l)
IDX=$(( $(od -An -N2 -tu2 /dev/urandom | tr -d ' ') % COUNT + 1 ))
ITEM_ID=$(printf '%s\n' "$IDS" | sed -n "${IDX}p")

curl -sf "${JELLYFIN_URL}/Items/${ITEM_ID}/Images/Backdrop/0" \
  -H "X-Emby-Token: ${JELLYFIN_API_KEY}" \
  -o "${OUTPUT}.tmp"

mv "${OUTPUT}.tmp" "${OUTPUT}"

docker restart homepage

echo "Rotated background to item ${ITEM_ID}"

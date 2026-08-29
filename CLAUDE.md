# neoflix — Claude instructions

Self-hosted media stack in Docker. For what/why, read
[README.md](README.md), [GOALS.md](GOALS.md), and
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) — this file doesn't repeat any
of that. It's just the pointers and house rules a fresh session needs
before touching anything.

## Working model

Most tasks here are **API calls against already-running containers**,
not code edits — adding a movie/episode, checking download status,
fixing a subtitle, checking for image updates. `docker-compose.yml` and
`scripts/` are the only things that are actually source code; changes
there are rare.

## Skills

- **`neoflix-add`** — adding a movie or episode. Handles the
  add/auto-grab/constrain/reconcile flow, including the standing size
  caps (movies ≤3GB, episodes ≤2GB) and bailing out cleanly (no
  download, reports back) if something hasn't aired/released yet or
  nothing valid is available.
  ```
  neoflix-add ted lasso s04e05
  neoflix-add as tears go by 1988
  ```
- **`neoflix-fix-subs`** — subtitle troubleshooting for one title.
  **Manual only** — don't run this proactively after an add; only when
  the user reports an actual problem (missing, won't show, out of sync,
  mistranslated) with a specific title's subtitles.
  ```
  neoflix-fix-subs the subs for hard boiled are out of sync
  neoflix-fix-subs police story subtitles don't match the video
  ```

## Quick reference

| Service | Port | Notes |
|---|---|---|
| Jellyfin | 8096 | media server |
| Radarr | 7878 | movies |
| Sonarr | 8989 | TV |
| Prowlarr | 9696 | indexers |
| qBittorrent | 8080 | download client — WebUI credentials are not recoverable (only a password hash is in config); if the browser session isn't already logged in, ask the user rather than trying to bypass it |
| Jellyseerr | 5055 | request UI |
| Bazarr | 6767 | subtitles |
| Homepage | 3000 | dashboard |
| Uptime Kuma | 3001 | monitoring |
| Lifecycle | 8100 | custom service — merges Jellyseerr/Radarr/Sonarr into one per-request status for Homepage's widget |

API keys: Radarr/Sonarr/Jellyfin/Jellyseerr keys are in `.env`. Bazarr's
**own** key (for calling its API directly) is under `auth:` in
`config/bazarr/config/config.yaml` — a different key from the
Radarr/Sonarr ones that also live in that same file.

Container paths are `/data/media/...` and `/data/downloads/...`; host
paths are `${DATA_ROOT}/media/...` and `${DATA_ROOT}/downloads/...` (see
`.env` for `DATA_ROOT`).

## House rules

1. **`downloads/` and `media/` share disk blocks via hardlinks**
   (decision #1). `du` gives the real number; Finder's Get Info and any
   naive byte-sum tool will double-count and look alarmingly larger than
   reality. Deleting a title from Radarr/Sonarr only removes the
   `media/` copy — the `downloads/` copy (and its qBittorrent torrent)
   sticks around using real space until removed separately.
2. **Check `df -h` on `$DATA_ROOT` before large downloads.** Free space
   on this box has swung between ~15GB and ~50GB+ within single
   sessions — don't assume yesterday's number still holds.
3. **A Radarr/Sonarr release grab (`POST /api/v3/release`) often returns
   an empty-looking/all-default response even on success.** Never treat
   that response body as a pass/fail signal — verify via
   `GET /api/v3/queue` or `/api/v3/history`.
4. **Sonarr's `addOptions.monitor` silently overrides per-season
   `monitored` flags** set in the same add payload, even when you also
   set them explicitly. Verify monitoring state with a fresh `GET` after
   any add — don't trust what you just posted.
5. **New episodes/movies often land in Jellyfin with a placeholder title
   and no overview** even after a library-level refresh. A forced
   per-item refresh (`ReplaceAllMetadata=true&ReplaceAllImages=true`)
   fixes it; a library-wide refresh alone often doesn't.
6. **Docker Desktop networking is NAT'd, not host** — UPnP/NAT-PMP
   requested from inside a container does not reach the real home
   router. Anything relying on auto-discovery (e.g. a client finding
   Jellyfin on the LAN by broadcast) is unreliable here; manual
   IP:port entry is the dependable path. Relatedly, the Mac's LAN IP has
   changed more than once this project's life — a DHCP reservation in
   the router would fix this permanently but hasn't been set up yet.
7. **Jellyfin transcode load can make the whole box feel slow**, not
   just playback — check `docker stats jellyfin` and
   `GET /Sessions` (look at `TranscodingInfo.TranscodeReasons`) before
   assuming a general system problem. `SubtitleCodecNotSupported` on an
   otherwise direct-playable file is the most common cause seen so far.

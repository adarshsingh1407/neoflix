# neoflix — Setup From Scratch

A complete walkthrough for setting this up on a brand-new machine, using only
`docker-compose.yml` from this repo. No prior context needed — if you just
want the *why* behind these choices, see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md),
but you don't need to read it to get running.

This guide includes a bunch of gotchas that weren't obvious the first time
through — following it in order avoids re-discovering them the hard way.

## 1. Prerequisites

- Docker Desktop installed and running
- Basic terminal comfort (copy/paste commands, no scripting needed)

## 2. Get the files

Clone this repo, or just copy `docker-compose.yml`, `.env.example`, and
`.gitignore` into a folder of your own.

## 3. Configure `.env`

```sh
cp .env.example .env
```

Edit `.env` and fill in:

- `PUID` / `PGID` — your OS user's UID/GID, so containers write files you
  own instead of root. Find them with:
  ```sh
  id -u   # PUID
  id -g   # PGID
  ```
- `TZ` — your timezone, e.g. `America/New_York`. On macOS:
  ```sh
  readlink /etc/localtime | sed 's#.*/zoneinfo/##'
  ```
- `DATA_ROOT` — an **absolute path outside this repo folder** where media
  and app config will live, e.g. `/Users/you/neoflix-data`. Keeping it
  outside the repo means it's never at risk of being swept into git.

## 4. Create the data folder structure

```sh
DATA_ROOT=/Users/you/neoflix-data   # match what you put in .env

mkdir -p "$DATA_ROOT"/downloads/{movies,tv,music,books,audiobooks,comics}
mkdir -p "$DATA_ROOT"/media/{movies,tv,anime,music,books,audiobooks,comics}
mkdir -p "$DATA_ROOT"/config/{jellyfin,sonarr,radarr,prowlarr,qbittorrent,jellyseerr,bazarr}
```

Only `movies`, `tv`, and `anime` under `media/` actually get used by this
compose file out of the box — the rest (`music`, `books`, `audiobooks`,
`comics`) are reserved for later if you ever add Lidarr/Readarr/a comics
manager. Costs nothing to have them ready.

**Key rule, don't skip this:** `downloads/` and `media/` must both live
under the same `DATA_ROOT` and be mounted as one parent volume (not two
separate mounts) into Radarr/Sonarr/qBittorrent — that's what lets those
apps hardlink a finished download into the library instead of copying it
(saves disk space). The compose file already does this correctly; just
don't restructure the folders differently.

## 5. Start the stack

```sh
docker compose up -d
docker compose ps   # confirm all 7 containers are "Up"
```

## 6. Initial setup — do these in order

Each app needs one-time setup after first boot. Web UIs are at
`http://localhost:<port>` (see table in [README.md](README.md)).

### 6.1 qBittorrent (`:8080`)

1. Get the auto-generated first-boot password:
   ```sh
   docker logs qbittorrent | grep -i "temporary password"
   ```
2. Log in with `admin` + that password, then immediately set a permanent
   one (Settings → Web UI) — the temporary one resets on every restart.
3. **Gotcha:** the default save path (`/downloads`) doesn't exist in this
   container — only `/data/downloads` does (from the shared mount). Go to
   Settings → Downloads and set the default save path to `/data/downloads`,
   or downloads will silently fail to write.
4. Optional but recommended: Settings → Downloads → Categories, add
   `radarr` → save path `/data/downloads/movies` and `tv-sonarr` → save
   path `/data/downloads/tv`, so files land in tidy subfolders instead of
   one flat directory. (Radarr/Sonarr auto-create these categories on
   first connection — you're just giving them a real save path afterward.)

### 6.2 Prowlarr (`:9696`)

1. Settings → Indexers → Add Indexer → pick a few well-known public
   trackers (no signup needed — search for e.g. "1337x", "YTS", "The Pirate
   Bay"). If one fails to add with an SSL/connection error, just retry —
   these trackers have real intermittent flakiness, it's not you.
2. **Don't skip this step** — Settings → Apps → Add Application → add both
   Radarr (`http://radarr:7878`) and Sonarr (`http://sonarr:8989`) with
   their API keys (find each app's key at Settings → General in that app).
   Without this, your indexers never actually reach Radarr/Sonarr, and
   every search comes back empty with no obvious error telling you why.

### 6.3 Radarr (`:7878`) and Sonarr (`:8989`)

For each app:
1. Settings → Download Clients → Add → qBittorrent (`qbittorrent`, port
   `8080`, your qBittorrent credentials from step 6.1)
2. Settings → Media Management → Root Folders → add `/data/media/movies`
   (Radarr) or `/data/media/tv` (Sonarr)
3. **Recommended:** Settings → Profiles → check your quality profiles.
   The default "HD-1080p" profile only accepts Bluray sources and will
   reject perfectly good WEBRip/WEBDL releases — a very common cause of
   "search found nothing" that's actually "search found things and
   rejected all of them." Either widen that profile or just use the
   built-in "Any" profile for anything you don't have strong preferences
   about.
4. (Sonarr only) Settings → Media Management → check "Season Folders" is
   enabled, so episodes get organized into per-season subfolders.

### 6.4 Jellyseerr (`:5055`)

Run the setup wizard: pick Jellyfin as your media server (hostname
`jellyfin`, port `8096`), sign in, then add Radarr and Sonarr as backing
servers using the same details as step 6.3 (container hostnames, not
`localhost`).

**Recommended:** when adding each server, set the default Quality Profile
to something permissive (like "Any") rather than "HD-1080p" — this is
Jellyseerr's own copy of the same quality-profile setting from 6.3, and it's
what actually gets applied to new requests.

If you want a separate **Anime** library (see 6.6), also fill in the
"Anime Series Type" (set to `anime`), "Anime Quality Profile," and "Anime
Root Folder" fields under the Sonarr server config — this makes Jellyseerr
auto-detect anime requests and route them correctly, no manual correction
needed per-show.

### 6.5 Jellyfin (`:8096`)

Run the setup wizard: create an admin account, then add libraries:
- **Movies** → content type "Movies" → path `/data/media/movies`
- **Shows** → content type "Shows" → path `/data/media/tv`
- (Optional) **Anime** → content type "Shows" → path `/data/media/anime`,
  if you set up the separate anime folder in step 4/6.4

### 6.6 Bazarr (`:6767`) — optional, for automatic subtitles

Skip this if you don't care about subtitles, or only want them occasionally
(Jellyfin has its own on-demand OpenSubtitles plugin for that lighter case —
Dashboard → Plugins → Catalog → install "Open Subtitles", then enter your
own OpenSubtitles.com account under its config page).

For automatic, whole-library subtitle fetching:
1. Settings → Radarr / Sonarr → fill in hostname (`radarr`/`sonarr`), port,
   API key — same details as everywhere else
2. Settings → Languages → Add New Profile → add your language(s) (e.g.
   English) → save
3. Still on Languages: set this profile as the **default** for both Movies
   and Series (separate dropdowns further down the page)
4. Settings → Providers → Add → OpenSubtitles.com → enter your own account
   credentials
5. Click **Save**

**Why Bazarr needs a different mount than Jellyfin:** Jellyfin's media mount
is intentionally **read-only** (limits what a media-serving app can touch).
Bazarr's is **read-write**, because it needs to write actual subtitle files
next to your videos. If you ever see Jellyfin's own subtitle plugin "succeed"
but the `.srt` never appears next to the video file, that's why — it fell
back to saving inside Jellyfin's internal config folder instead. Not a bug,
just a consequence of the read-only mount; Bazarr is the fix.

## 7. Verify it works

1. Open Jellyseerr, search for anything, hit Request
2. Check Radarr/Sonarr's Activity tab — should show it getting searched and
   grabbed within a minute or so
3. Check qBittorrent — should show it downloading
4. Once done, check Jellyfin — should appear in the library automatically,
   no manual file moves

If a search comes back empty, re-check step 6.2 (Prowlarr → Apps) and 6.3
step 3 (quality profile) first — those two cause the vast majority of
"nothing's happening" cases.

## 8. Known rough edges

- **Intermittent connection failures** to external sites (TMDB, indexers,
  subtitle providers) happen occasionally and usually resolve on a simple
  retry — this seems to be generic Docker Desktop networking flakiness, not
  specific to any one service. Don't over-troubleshoot a single failure;
  just try again first.
- **Radarr/Sonarr don't import extra files** (subtitles, `.nfo`, images)
  alongside the video by default — if a release bundles subtitles and you
  don't have Bazarr, you'll need to manually copy them from the download
  folder into the library folder, or install Jellyfin's subtitle plugin
  (6.6) for on-demand fetching instead.
- **Host IP can drift** if your router uses dynamic DHCP leases — if a
  device that worked yesterday can't connect today, check the Mac's
  current IP (`ipconfig getifaddr en0` on macOS) before assuming something
  broke. A DHCP reservation in your router settings fixes this permanently.
- **Image tags are `:latest`**, not pinned — good for a POC (auto-updates),
  but reconsider before running this unattended long-term, since an upstream
  update could occasionally break something without warning.

## What's next

Once this is running, see [USER_STORIES.md](USER_STORIES.md) for a more
detailed manual test plan, and [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) if
you're curious why any of this is built the way it is.

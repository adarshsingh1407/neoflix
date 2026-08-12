# neoflix — POC Manual Test Stories

How to manually validate the running stack once it's up: M1 Mac (Docker
Desktop, running the core acquisition/playback containers) + iPhone
(Jellyfin Mobile app + Safari) on the same LAN. Each story is a
self-contained pass/fail check. Run them in order — later stories assume
earlier ones passed.

Scoped to the original POC pipeline (decisions 1-8: Jellyfin, Radarr,
Sonarr, Prowlarr, qBittorrent, Jellyseerr, Bazarr). The optional add-ons
from decisions 9-12 (Homepage, Ofelia, Uptime Kuma, Jellyfin Vue) aren't
covered by these stories — Uptime Kuma's own status page is effectively
its live health check, and the others have no acquisition/playback
behavior to validate this way.

Test content (per [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) decision #3):
- **Movie:** *Big Buck Bunny* (Blender Foundation)
- **Show:** *Pioneer One* S01E01

**Performed by:** each story is tagged `[Claude]` or `[You]`. `[Claude]`
covers anything reachable from the Mac — containers, filesystem, each app's
web UI or REST API — drivable via Bash/curl or browser automation. `[You]`
is reserved for the physical iPhone stories: there's no way to control an
actual phone remotely, so playback and on-device UX checks are yours by
nature of what they're testing.

---

## 1. Stack is up `[Claude]`

**As** the admin, **I want** the core pipeline's containers running and
their web UIs reachable from the Mac's browser, **so that** I know the
stack booted correctly before configuring anything.

Steps:
1. `docker compose ps` — Jellyfin, Radarr, Sonarr, Prowlarr, qBittorrent,
   Jellyseerr, and Bazarr all show `running`/`healthy`.
2. Open each in a Mac browser tab: Jellyfin (`:8096`), Radarr (`:7878`),
   Sonarr (`:8989`), Prowlarr (`:9696`), qBittorrent (`:8080`), Jellyseerr
   (`:5055`), Bazarr (`:6767`) — adjust ports to whatever's finalized in
   decision #8.

**Pass:** all seven load their setup/login screen. No restart loops in
`docker compose ps`.

---

## 2. Indexers are wired up (and Pioneer One pre-flight check) `[Claude]`

**As** the admin, **I want** Prowlarr's indexers added and synced to
Radarr/Sonarr, **so that** title searches actually return results.

Steps:
1. In Prowlarr, add 2–3 well-known public tracker indexers (decision #4).
2. In Prowlarr → Settings → Apps, add Radarr and Sonarr (paste each app's API
   key, use container-name URLs e.g. `http://radarr:7878` since everything's
   on the shared bridge network — decision #5).
3. **Pre-flight check, before wiring up Jellyseerr:** manually search
   "Pioneer One" in Prowlarr's search tab. Note whether it returns a
   reasonably-seeded result.

**Pass:** Prowlarr shows Radarr/Sonarr as synced apps. Search for "Big Buck
Bunny" returns results.

**If Pioneer One's search comes up empty or dead:** not a failure — per the
decision #3 addendum, fall back to grabbing it directly from [Internet
Archive](https://archive.org/details/PioneerOneS01E01) (torrent/magnet link,
added to qBittorrent manually) and skip to story 5, step 2 (manual add)
instead of story 4's Jellyseerr request flow for the show specifically. The
movie test alone still fully proves the automated pipeline.

---

## 3. Download client is connected `[Claude]`

**As** the admin, **I want** Radarr and Sonarr able to send grabs to
qBittorrent, **so that** a request actually results in a download.

Steps:
1. In Radarr and Sonarr, add qBittorrent as a download client
   (`http://qbittorrent:8080`, credentials from qBittorrent's WebUI).
2. Add root folders: Radarr → `/data/media/movies`, Sonarr →
   `/data/media/tv` (container-side paths per decision #1's shared mount).

**Pass:** "Test" button succeeds on both the download client and root folder
config in Radarr and Sonarr.

---

## 4. Jellyseerr is connected and the search/request UX works `[Claude]`

**As** the end user, **I want** one search UI where I type a title and it
gets found and added, **so that** I don't have to use Radarr and Sonarr's
UIs separately (the whole reason Jellyseerr is in scope — decision #6).

Steps:
1. Complete Jellyseerr's setup wizard: sign in using your Jellyfin account,
   add Radarr and Sonarr as its backing servers.
2. From a Mac browser, open Jellyseerr, search "Big Buck Bunny," click
   Request.
3. Search "Pioneer One," click Request for S01E01 (skip this step if the
   story 2 pre-flight check failed — use the archive.org fallback instead).

**Pass:** both requests show as "Processing"/"Pending" in Jellyseerr, and
correspondingly appear in Radarr's/Sonarr's "Wanted" list within a minute or
two.

---

## 5. Content downloads and lands in the library automatically `[Claude]`

**As** the admin, **I want** the finished download to appear in
`~/neoflix-data/media/` without manual file moves, **so that** the "no manual
file wrangling" goal (GOALS.md) is proven.

Steps:
1. Watch qBittorrent's UI — download should appear and progress to 100%.
2. Watch Radarr's/Sonarr's Activity/History tab — status should move from
   "Grabbed" → "Downloaded" → "Imported."
3. **Manual fallback (only if story 2's pre-flight failed):** download
   Pioneer One's torrent/magnet directly from Internet Archive, add it to
   qBittorrent manually, and manually trigger a Sonarr import once it
   completes (Sonarr → Wanted → Manual Import), pointing at the downloaded
   file.
4. Check the Mac filesystem directly: `ls ~/neoflix-data/media/movies` and
   `ls ~/neoflix-data/media/tv` — files should be present without you having
   moved anything.
5. (Optional, ties to the hardlink verification noted in decision #1) —
   compare `ls -i` inode numbers between the file in `downloads/` and
   `media/` to see whether it was hardlinked or copied. Either is a pass for
   this POC; just worth knowing.

**Pass:** both files appear under `media/` automatically. No manual `mv`/`cp`
performed by you (aside from the Pioneer One fallback path, if triggered).

---

## 6. Jellyfin library reflects the new content `[Claude]`

**As** the end user, **I want** the movie/episode to show up in Jellyfin
without extra manual work, **so that** it's actually watchable.

Steps:
1. Open Jellyfin on the Mac browser.
2. If content doesn't appear within a few minutes, manually trigger a
   library scan (Dashboard → Libraries → Scan All Libraries).
3. Confirm *Big Buck Bunny* and *Pioneer One* S01E01 both appear with
   correct titles/artwork (metadata pulled automatically).

**Pass:** both titles visible and browsable in Jellyfin's UI.

---

## 7. Playback on iPhone — Jellyfin app `[You]`

**As** the end user, **I want** to play the movie on my iPhone over LAN via
the Jellyfin Mobile app, **so that** the full pipeline is validated on the
actual target client device (decision #5).

Steps:
1. Confirm iPhone is on the same WiFi network as the Mac.
2. Open Jellyfin Mobile app, add server manually:
   `http://<mac-lan-ip>:8096` (find the Mac's LAN IP via System Settings →
   Wi-Fi → Details, or `ipconfig getifaddr en0` in Terminal).
3. Log in, browse to *Big Buck Bunny*, tap play.
4. Confirm video and audio play smoothly; note whether it shows "Direct
   Play" or "Transcoding" (expect possible software transcoding per GOALS.md
   non-goals — either is a pass, just worth noting which).

**Pass:** movie plays back on the iPhone app without errors.

---

## 8. Playback on iPhone — Safari (browser fallback) `[You]`

**As** the end user, **I want** to confirm playback also works without the
app installed, **so that** the browser path (mentioned as a fallback in
decision #5) is validated too.

Steps:
1. Open Safari on iPhone, navigate to `http://<mac-lan-ip>:8096`.
2. Log in, browse to *Pioneer One* S01E01, tap play.

**Pass:** episode plays back in Safari without errors.

---

## 9. Search/request UX on the actual client device `[You]`

**As** the end user, **I want** to confirm Jellyseerr's search-and-request
flow (story 4) also works from the iPhone, not just the Mac, **so that** the
UX this was added for (decision #6) is validated on the device it'll
actually be used from.

Steps:
1. On iPhone Safari, navigate to `http://<mac-lan-ip>:5055` (Jellyseerr).
2. Search for a third, arbitrary title (not one already requested) and
   confirm the request goes through.

**Pass:** request succeeds from the iPhone browser; appears in
Radarr/Sonarr's Wanted list shortly after (no need to let it fully download —
this story just validates the request UX end-to-end from the client device).

---

## Overall POC success

Matches [GOALS.md](GOALS.md)'s three success criteria, now concretely:
stories 1–4 prove "search → grabbed automatically," story 5 proves
"organized without manual file moves," stories 6–8 prove "plays back on a
LAN device." Story 9 is a bonus check specific to the Jellyseerr addition.

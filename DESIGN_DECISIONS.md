# neoflix — Design Decisions

Log of architecture decisions, worked through most-foundational-first (see roadmap
at the bottom). Each entry captures the decision, the rationale, and what was
explicitly deferred — update this file as decisions are made, don't just talk
through them in chat.

## Decisions

### 1. Storage & folder layout — DECIDED

**Location:** Data lives outside the git-tracked `neoflix/` project folder, at
`~/neoflix-data/` (sibling to the repo, not inside it). Keeps media/binary/db
files out of git entirely — no `.gitignore` juggling, no risk of a large file
getting committed by accident.

**Structure:**
```
neoflix-data/
├── downloads/
│   ├── movies/
│   ├── tv/
│   ├── music/
│   ├── books/
│   ├── audiobooks/
│   └── comics/
├── media/
│   ├── movies/
│   ├── tv/
│   ├── music/
│   ├── books/
│   ├── audiobooks/
│   └── comics/
└── config/
    ├── jellyfin/
    ├── sonarr/
    ├── radarr/
    ├── prowlarr/
    ├── qbittorrent/
    └── jellyseerr/
```

**Key rule:** `downloads/` and `media/` must be mounted from the *same parent*
(`neoflix-data/` as one volume) into Sonarr/Radarr/qBittorrent — not as two
separate volumes. This is what lets those apps hardlink instead of copy when
moving a finished download into the library, avoiding doubled disk usage.

**Verified during design review (2026-07-19):** couldn't find a definitive
confirmation that Docker Desktop's Mac virtualization layer (virtiofs)
preserves hardlink behavior identically to native Linux — plausible that
Sonarr/Radarr silently fall back to copying if it hits a cross-device-link
error inside the VM. Decided to verify empirically once real imports were
happening (compare inode numbers or watch disk usage) rather than
architecting around a maybe-problem up front.

**Confirmed working:** `ls -li` on an imported file shows the same inode
number and a link count of 2 between its `downloads/` and `media/` copies
— virtiofs does preserve hardlinks correctly here. One real, practical
consequence worth knowing: deleting a title from Radarr/Sonarr only
removes the `media/` copy, so its `downloads/` copy (and qBittorrent
torrent) keeps the disk space allocated until removed separately — this
tripped up more than one disk-space investigation before the pattern was
recognized. Also: Finder's Get Info and other naive size tools sum every
filename's apparent size without deduplicating hardlinks, so they report
roughly double the real usage for anything still linked both places —
`du` gives the real number.

**Media types beyond movies/TV:** folders for `music/`, `books/`, `audiobooks/`,
`comics/` are reserved now (costs nothing) but **no corresponding apps are
deployed** — no Lidarr, Readarr, or comics manager in the POC. `config/` only
has entries for apps actually running (Jellyfin, Sonarr, Radarr, Prowlarr,
qBittorrent). Adding a new media type later is additive: new folder pair already
exists, just add the app.

**POC scope:** validated with exactly one movie and one show, not a broader
library test.

---

### 2. Content acquisition method: torrent vs Usenet — DECIDED

**Decision:** Torrent (qBittorrent + Prowlarr indexers).

**Rationale:** Free, zero signup friction, matches what virtually every
reference stack/guide defaults to. Usenet (SABnzbd) would be faster and more
private by architecture (no P2P swarm exposure), but requires a paid provider
subscription *and* usually a paid NZB indexer — real ongoing cost not justified
just to validate a pipeline with one movie and one show. Usenet remains a
legitimate later upgrade if torrent speed/reliability becomes a real pain point
once this is more than a POC.

---

### 3. Download client VPN exposure — DECIDED (final, after two revisions)

**Decision:** No VPN for the POC. No gluetun, no provider subscription, $0 cost.

**Decision history** (kept for context, not just the end state):
1. Originally "skip VPN," on the assumption POC content would be legal/open.
2. Reversed to "include gluetun + Mullvad" once the indexer discussion (#4)
   surfaced that there was no legal/open equivalent to test a TV show with —
   the show test would've meant real content on a public tracker, unprotected.
3. Reverted back to "skip VPN" after confirming **Pioneer One** (2010,
   Creative Commons, distributed via VODO specifically as a legal-BitTorrent
   showcase) closes that exact gap — it's the TV-show equivalent of the
   Blender Foundation open films.

**Final POC test content, both legal/open, resolving the exposure concern
cleanly this time:**
- 1 movie — a Blender Foundation open film (e.g. *Big Buck Bunny*)
- 1 episode of 1 show — *Pioneer One* (S01E01)

**Deferred, not forgotten:** VPN (gluetun + Mullvad) is still the right call
*before downloading anything beyond this POC's open-content test set* — e.g.
before grabbing a real movie/show you actually want to watch. Retrofit path
if/when needed: add a `gluetun` service, set qBittorrent's
`network_mode: "service:gluetun"`, move its port publishing onto gluetun, add
a health-check `depends_on` so qBittorrent never runs unprotected.

**Update (running setup):** this deferred item was never picked back up.
The stack now acquires real titles, and it still runs with **no VPN** —
qBittorrent connects directly from the host's own IP. That is the current,
accepted state rather than a re-made decision; the retrofit path above is
still the way to add protection if that changes.

**Verified during design review (2026-07-19):** Pioneer One's original VODO
swarm is 15 years old — likely low/dead seeders on the generic public
trackers Prowlarr indexes by default (decision #4), which probably never
carried this niche 2010 release to begin with. Fallback: it's hosted directly
on [Internet Archive](https://archive.org/details/PioneerOneS01E01), which
typically keeps its own items' torrents permanently seeded via IA's own
infrastructure — use that torrent/magnet directly in qBittorrent if Prowlarr's
search comes up empty. Doesn't block the POC's core purpose either way — the
*Big Buck Bunny* test alone already fully exercises the automated
search→grab→organize→play pipeline; the show test's fallback (if needed) just
means one manual grab instead of an indexer hit, not a broken pipeline.

---

### 4. Indexers (Prowlarr) — DECIDED

**Decision:** Public trackers via Prowlarr's built-in indexer definitions
(e.g. 1337x, YTS, EZTV — well-known, reliable ones). No signup required, just
enabled in Prowlarr's UI.

**Rationale:** Free and zero-friction, sufficient to prove the pipeline works
end-to-end for a one-movie/one-show POC. Private trackers offer better
curation/speed but require an application process and an ongoing seed-ratio
obligation — real commitment not justified for a POC. Jackett not needed;
Prowlarr has absorbed public-tracker support natively.

**Deferred:** private tracker membership, if this becomes a permanent setup
and release quality/speed starts to matter.

**Verified during execution (2026-07-19):** added YTS, The Pirate Bay,
Knaben, and LimeTorrents (1337x blocked by Cloudflare without a proxy we're
not running; EZTV and Internet Archive hit DNS/SSL connection errors from
inside the container — not investigated further since coverage was already
sufficient). Pre-flight search confirmed the decision #3 concern was
real-but-not-blocking: both test titles return thin swarms (0-2 seeders)
rather than being dead. Notably found *Pioneer.One.S01E01.720p.x264-VODO*
(the exact original release) at 2 seeders on The Pirate Bay — given both
files are small, this is workable without needing the Internet Archive
manual-fallback plan.

---

### 5. Docker networking model — DECIDED

**Decision:** Custom Docker bridge network with explicit `ports:` publishing —
not host networking. All POC containers (Jellyfin, Sonarr, Radarr, Prowlarr,
qBittorrent) share one bridge network and address each other by container
name (e.g. Radarr → `http://prowlarr:9696`). Clients connect via
`http://<mac-lan-ip>:8096`, entered manually — no auto-discovery.

**Rationale:**
- Docker Desktop for Mac only gained `--network host` support in version
  4.34+, and even then it's VM-scoped (containers share the Linux VM's
  network namespace, not the Mac's actual interface), requires Docker account
  sign-in, and has known port-binding/IP-reporting quirks. Not the reliable
  path that Linux-centric guides assume when they recommend host networking
  for Jellyfin.
- Bridge networking + published ports is fully supported on Docker Desktop,
  no feature flags, no VM networking edge cases.
- Trade-off is losing DLNA/SSDP auto-discovery (doesn't traverse container
  NAT in bridge mode) — a one-time convenience loss, not a functional
  blocker, since every Jellyfin client supports manually entering the server
  address.

**Validated by:** POC client is iPhone (Jellyfin Mobile app + Safari), not a
TV — same LAN/bridge setup applies regardless of client device, confirmed
working for both the app and browser paths.

---

### 6. Component scope — DECIDED (revised)

**Decision:** Jellyfin, Radarr, Sonarr, Prowlarr, qBittorrent, **Jellyseerr**.

**Revised from "Jellyseerr deferred"** once it became clear that what was
being asked for — one search UI where you type a title and it gets found and
added, rather than searching separately in Radarr and Sonarr — is exactly
Jellyseerr's purpose. It sits in front of Radarr/Sonarr (via their API keys)
and Jellyfin (for auth), unifying movie + show search/request into one UI.
Moderate addition: one more container, one more config folder, no change to
storage (#1), networking (#5), or the "no VPN for POC" call (#3).

**Deferred (unchanged):**
- Lidarr, Readarr (books/audiobooks), comics manager (Mylar3/Kapowarr) —
  folders already reserved (decision #1), apps not deployed until actually
  needed.
- Bazarr (subtitles), monitoring — not needed to prove the pipeline for a
  single movie + single show episode.

**Rationale:** POC success criteria is narrow (1 movie + 1 show episode,
search → grab → play), but the search/request UX is core enough to what was
actually being asked for that it's worth including now rather than bolting on
after. Everything else stays deferred — no scope creep beyond this one addition.

---

### 7. Compose structure & secrets/env management — DECIDED

**Compose structure:** single `docker-compose.yml` at `neoflix/docker-compose.yml`
— one file, six services (Jellyfin, Radarr, Sonarr, Prowlarr, qBittorrent,
Jellyseerr) plus the shared bridge network from decision #5. No base/override
split or per-service `include:` files — that indirection only pays off with
multiple deployment environments or a much larger service count, neither of
which applies here.

**Secrets:** there isn't really a credential that needs to flow through the
compose file for this scope. Sonarr/Radarr/Prowlarr/Jellyseerr each generate
their own API key on first boot, stored in their own config database
(`~/neoflix-data/config/<app>/`, outside git per decision #1); keys are wired
together through each app's web UI as a one-time setup step during execution,
not templated into compose. Same for qBittorrent's WebUI login. No VPN
credentials yet (decision #3 deferred that).

**What is externalized:** non-secret shared values — `PUID`, `PGID`, `TZ`,
`DATA_ROOT` (→ `~/neoflix-data`) — via a **`.env`** file at the project root,
referenced in compose as `${PUID}` etc.

**Git handling** (pushed to a **public** GitHub repo):
- `.env` and `credentials.env` — gitignored. They now hold real values:
  the API keys and logins that `scripts/bootstrap.py` generates, plus
  machine-specific paths. They must never be committed, and a VPN
  credential (decision #3's deferred item) would live in the same place.
- **`.env.example`** — committed, placeholder values, documents the expected
  variables.
- `~/neoflix-data/` (media, downloads, all app config/databases) already
  lives entirely outside the `neoflix/` project folder (decision #1) — so it
  was never going to be swept up in a `git add`.
- Because the repo is public, nothing secret may appear in any committed
  file, doc or commit message — use placeholders such as
  `<JELLYFIN_API_KEY>`. Check pending changes for real key values before
  committing.

---

### 8. Execution details — DECIDED

**Decision:** see [docker-compose.yml](docker-compose.yml) — that file is the
source of truth for ports/mounts/env vars, not duplicated here (per the
LLD-lives-in-code call made earlier). A few choices worth flagging that
aren't self-evident from reading the YAML:

- **Image tags: `:latest`**, not pinned versions. Deliberate tradeoff — loses
  reproducibility, gains zero-maintenance for a personal setup that isn't
  running unattended long-term. Revisit if/when this moves to permanent
  hardware (decision worth re-litigating then, not now).

  **Update:** the tradeoff assumes `:latest` reliably means "current" —
  checked all ten images directly and found one exception: Uptime Kuma's
  own `:latest` tag on Docker Hub hadn't been rebuilt in ~9 months (its own
  registry metadata confirmed it, still resolving to `v1.23.17` against a
  then-current `v2.5.0`), a known, maintainer-acknowledged issue upstream.
  Pinned to `2.5.0` explicitly rather than trusting `:latest` for that one
  image; the other nine were all confirmed current at the time of checking.
- **Radarr/Sonarr/qBittorrent mount the entire `${DATA_ROOT}` as `/data`**
  (not just `downloads/` + `media/`), so `config/` is technically visible
  under `/data/config/` inside those containers too. Harmless (apps don't
  touch what they don't need) and matches the common TRaSH-guides pattern;
  each app's *own* config is still mounted separately as `/config`.
- **Jellyfin only mounts `media/`, read-only** — it never needs `downloads/`
  or other apps' config, and read-only limits blast radius if anything in
  Jellyfin ever tried to write there.
- **qBittorrent gets an explicit `TORRENTING_PORT=6881`** (published as both
  TCP and UDP) so the peer-facing port is stable across restarts instead of
  randomizing. First boot generates a temporary WebUI password — retrieve it
  via `docker logs qbittorrent`, not something to hardcode in compose.
- **`restart: unless-stopped`** on all services, so the stack survives a
  Docker Desktop restart without a manual `up` each time.
- Compose file validated with `docker compose config` before being treated
  as done.

**`~/neoflix-data/` folder structure created** matching decision #1, ahead of
first `docker compose up`.

**Update (2026-07-19): Jellyseerr → Seerr migration.** Jellyseerr merged with
Overseerr into a unified project renamed "Seerr" in Feb 2026 — the old image
(`fallenbagel/jellyseerr:latest`) hadn't been rebuilt in ~11 months (confirmed
via image build date) and is effectively abandoned. Migrated to
`ghcr.io/seerr-team/seerr:latest` (v2.7.3 → v3.3.0). Notes for anyone touching
this compose file again:
- New image runs as a fixed UID 1000, not PUID/PGID like the linuxserver
  images — had to `chown -R 1000:1000` the existing
  `config/jellyseerr/` folder before first boot.
- No longer bundles its own init process — needs `init: true` in the
  compose service definition, or it won't reap zombie processes correctly.
- Migration reads the existing config/database automatically on first boot
  as long as the same volume mount is preserved — no manual data migration
  needed. Verified Radarr/Sonarr connections and quality profile settings
  carried over intact.
- Container name/service key left as `jellyseerr` for continuity with the
  rest of this repo's docs, even though the upstream project renamed itself.

---

### 9. Dashboard (Homepage) — DECIDED

**Decision:** `ghcr.io/gethomepage/homepage:latest`, one dashboard with
quick-launch links plus live widgets for Jellyfin, Jellyseerr, Radarr,
Sonarr, Bazarr, qBittorrent, and Uptime Kuma (#11).

**Rationale:** solves "which port was that again" cheaply — one more
container, reuses each app's existing API key (already generated on first
boot per decision #7), no new secrets category beyond what's already
externalized via `.env`.

**Config is static YAML** under `config/homepage/` (`services.yaml`,
`widgets.yaml`, `settings.yaml`, `bookmarks.yaml`, `custom.css`) — unlike
Radarr/Sonarr/etc., there's no setup wizard. This lives in `DATA_ROOT`
per decision #1, so it's **not version-controlled or templated in this
repo** — a fresh clone starts Homepage with an empty, unconfigured
dashboard. Hand-authoring that YAML is closer in effort to configuring a
new app's web UI than to the zero-effort apps; not something `docker
compose up -d` alone gets you for this one.

**Deferred:** templating `services.yaml` from `.env`-driven values so a
fresh clone gets a working dashboard automatically.

**Follow-up, open (2026-09-09) — Jellyfin widget broken by server 12.0 upgrade:**
upgraded Jellyfin (`lscr.io/linuxserver/jellyfin:latest`) to 12.0, which removed
the legacy `/emby/` API prefix outright. Homepage's built-in `type: jellyfin`
widget (image built 2026-08-14, predates 12.0) still calls `/emby/Items/Counts`
and `/emby/Sessions`, so the dashboard's Jellyfin card started showing a red
"API Error Information" banner. No fix available in `ghcr.io/gethomepage/homepage`
as of this note (checked their releases — latest is `v2.2.0`, 2026-09-02, nothing
since addresses 12.0). Confirmed the modern `/Sessions` and `/Items/Counts`
endpoints (no `/emby/` prefix) both work fine on 12.0.

**Temporary workaround in place:** swapped the Jellyfin service's `widget:`
block in `services.yaml` (outside git, per above) to a `type: customapi` widget
hitting `/Items/Counts` directly with the modern `Authorization: MediaBrowser
Token="..."` header, showing Movies/Series/Episodes counts. Loses the original
widget's live session/now-playing info (`enableUser`/`showEpisodeNumber`) —
`customapi` only queries one endpoint per widget, and counts were the simpler
single-endpoint replacement.

**Revert trigger:** once `ghcr.io/gethomepage/homepage` ships a release whose
Jellyfin widget supports 12.0, swap `services.yaml`'s Jellyfin `widget:` block
back to the original (kept as a comment alongside the workaround in that file):
```yaml
widget:
  type: jellyfin
  url: http://jellyfin:8096
  key: <JELLYFIN_API_KEY>
  enableUser: true
  showEpisodeNumber: true
```

**Personalizations on this install (2026-11-06, outside git):** because
`services.yaml` isn't version-controlled (above), this is the record of
what the live dashboard has that `bootstrap.py`'s starter doesn't.
`bootstrap.py` never overwrites an existing `services.yaml`, so these
survive re-runs; a fresh install has to redo them by hand.

- **Jellyseerr card simplified:** removed the `customapi` dynamic-list
  widget that read `http://lifecycle:8100/status` (the per-title
  "Requested / Available now" list from #14). The card now shows only the
  built-in `type: seerr` request counts. The `custom.css` grid that makes
  Jellyseerr span the right half of the Media section was sized for that
  list and was left as-is. To restore the list, re-add:
  ```yaml
  - type: customapi
    url: http://lifecycle:8100/status
    display: dynamic-list
    mappings:
      items: items
      name: name
      label: label
  ```
- **Network card added** (Monitoring section), with two `customapi`
  widgets:
  - **Public IP:** `https://api.ipify.org/?format=json`, every 5 min. This
    is the stack's only call to a third-party service purely for display.
    (Note: `api64.ipify.org` returned 404 from inside the container; the
    trailing slash on `api.ipify.org/` matters too.)
  - **LAN IP:** `http://lifecycle:8100/host`, every 60s. Containers can't
    see the Mac's LAN address (Docker Desktop NAT, see `CLAUDE.md` house
    rule 6), so `scripts/host/lan-ip.sh` writes it on the host into
    `${DATA_ROOT}/config/host/host.json`. A launchd agent
    (`com.neoflix.lan-ip`, installed with `scripts/host/lan-ip.sh install`)
    re-runs it on every network change and every 5 min. The LAN IP has
    changed more than once on this Mac, so a value saved once at setup
    would go stale exactly when it's needed. Lifecycle passes the file
    through because Homepage only serves `public/images` files that existed
    when it started (that's also why Ofelia restarts Homepage after a
    background change, #10). The folder is mounted, not the single file,
    so the script's write-then-rename shows up inside the container.
    macOS only; Linux/Windows hosts get `"unknown"` until there's an
    equivalent.

---

### 10. Scheduled automation (Ofelia) — DECIDED

**Decision:** `mcuadros/ofelia:latest` as a Docker-native cron scheduler,
configured entirely via labels on its own container — no separate config
file, no web UI. Two jobs:
- **`rotate-background`** (`@every 1h`) — picks a random backdrop from
  Jellyfin's own library via its API and sets it as Homepage's background,
  restarting Homepage after (a documented Homepage/Next.js requirement —
  it caches static assets and won't pick up a changed file otherwise).
- **`poster-grid`** (`0 4 * * *`) — generates a full-bleed poster-collage
  image per Jellyfin library and pushes it back as that library's cover,
  replacing the third-party `jellyfin-library-poster` tool (its output
  wasted ~40% of the canvas on a plain background and cropped posters at
  the edges; the custom version dynamically sizes its grid to the number
  of unique posters available so nothing repeats).

**Rationale:** keeps the whole stack "pure docker compose" — no system
cron, no separate task runner needed for either job. Ofelia's `job-run`
type spins up short-lived containers per run (`docker:cli`, `alpine:latest`,
with `curl`/`jq`/`python3`/`py3-pillow` installed inline via `apk add` at
run time) rather than needing a long-running custom image, so no
Dockerfile/build step was added to the repo.

**Secrets:** `JELLYFIN_API_KEY` is externalized via `.env`, same pattern as
decision #7 — it briefly ended up hardcoded directly in a job's label
before being caught and corrected.

**Verified:** Jellyfin's own OpenAPI spec claims its image-upload endpoint
(`POST /Items/{id}/Images/{type}`) takes raw binary, but the live server
actually base64-decodes the request body — confirmed via the server's own
stack trace after the naive raw-bytes version 500'd. The poster-grid script
base64-encodes before uploading to work around this spec/implementation
mismatch.

---

### 11. Uptime monitoring (Uptime Kuma) — DECIDED

**Decision:** `louislam/uptime-kuma:2.5.9` (pinned; its `:latest` tag went
stale — see the update under decision #8). The pin means it never updates
on its own, so it has to be raised by hand: bumped from `2.5.0` on
2026-11-06 for security fixes (an admin-authenticated RCE via Apprise
argument injection, an unauthenticated ping-badge DoS, and RSS cache
poisoning). Monitors all 8 web-facing
services over HTTP on the internal bridge network (`jellyfin`, `radarr`,
`sonarr`, `bazarr`, `prowlarr`, `qbittorrent`, `jellyseerr`, `homepage`),
backed by a public status page (slug `default`) that Homepage's widget
reads — Uptime Kuma has no stable full API, so status-page scraping is the
supported integration path.

**Update:** originally monitored `jellyfin-vue` instead of `homepage` —
swapped when decision #12 was reversed and Homepage itself needed
coverage instead (closing the exact blind spot that let the whole stack
go down silently after a Docker Desktop restart with nothing catching it).

**Rationale:** closes the gap between "links dashboard" and "actually know
if something's down" — Homepage's per-service widgets show data *from*
each app, but nothing previously said whether an app was simply
unreachable.

**Setup note:** needs a one-time admin account on first boot, same as every
other app here. Originally left manual since Uptime Kuma has no plain REST
API for it (it's socket.io-based) — decision #13 automated it anyway, via
the `uptime-kuma-api` library's `setup()` call over that same socket.io
connection.

---

### 12. Alternative Jellyfin client (Jellyfin Vue) — REMOVED

Tried as an optional second Jellyfin web client; removed after never being
used past the initial try — added nothing over the stock client for the
extra maintenance surface. Not part of the stack. Uptime Kuma's freed-up
monitor slot now watches Homepage instead (decision #11).

---

### 13. Automated post-setup (`scripts/bootstrap.py`) — DECIDED

**Problem:** a friend cloned the repo, followed SETUP.md, and still had to
manually create four separate accounts and wire up seven apps by hand
before anything worked — the "one command and you're done" promise of
`docker compose up -d` didn't actually hold past the containers starting.

**Decision:** a single script, `scripts/bootstrap.py`, that replaces
SETUP.md's old step 4 (folder creation) and step 6 (per-app manual setup)
entirely. The only manual input left is `credentials.env` (gitignored, same
pattern as `.env` — decision #7): one admin username/password reused
everywhere a new account gets created (qBittorrent, Jellyfin, Uptime Kuma),
plus an optional real OpenSubtitles account for Bazarr.

**What makes this feasible at all:** every `*arr`-family app (Radarr,
Sonarr, Prowlarr) writes its own auto-generated API key to `config.xml` on
first boot — readable straight off disk, no login required first. That's
the anchor the rest of the automation hangs off. Per-service approach:

- **qBittorrent** — same temp-password-from-logs flow a human would use,
  scripted (`docker logs` → login → `setPreferences` for permanent
  credentials + default save path).
- **Prowlarr** — indexers and Radarr/Sonarr app links added via its
  schema-driven REST API (fetch a field template by name, fill in values,
  POST).
- **Radarr/Sonarr** — download client, root folder, and an "Any" quality
  profile (everything allowed except `Unknown`/`Raw-HD`, matching what a
  human following the old SETUP.md steps would have picked) created the
  same schema-driven way.
- **Jellyfin** — its `/Startup/*` endpoints are a genuine, documented
  setup-wizard API (confirmed against the live server's own OpenAPI spec),
  not a hack. Creates the admin account, both libraries, and a permanent
  API key (also saved into `.env` as `JELLYFIN_API_KEY`, so Ofelia's
  poster-grid job — decision #10 — picks it up too).
- **Jellyseerr** — `POST /auth/jellyfin` doubles as account creation: sign
  in with the just-created Jellyfin admin account and, since no other user
  exists yet, Jellyseerr creates its own admin account from that login.
  Radarr/Sonarr get linked the same way as Prowlarr's app links.
- **Bazarr** — has no documented settings API (its public API is
  read/action endpoints only — history, wanted, providers — not
  configuration). Its settings *are* just a YAML file
  (`config/bazarr/config/config.yaml`) that Bazarr writes on first boot and
  reads on restart, so the script patches that file directly (Radarr/Sonarr
  connection info, OpenSubtitles credentials if provided) and restarts the
  container — the same "no wizard, it's just a config file" shape as
  Homepage.
- **Uptime Kuma** — turned out to be *not* the hardest piece, despite that
  assumption in decision #11. The `uptime-kuma-api` PyPI library wraps its
  socket.io protocol, including a `setup()` call that performs first-run
  account creation, so it's fully automatable — monitors and the public
  status page too. Decision #11 updated accordingly.
- **Homepage** — the most automatable of all, since it never had a wizard
  in the first place (decision #9): the script templates a minimal working
  `services.yaml`/`widgets.yaml`/`settings.yaml` from the API keys
  discovered above.

**Deliberately still manual, by design, not oversight:**
- **Bazarr's subtitle language profile** — stored in Bazarr's own SQLite
  database (`table_languages_profiles`), not a file or any documented API.
  Writing to a live app's internal DB schema directly was judged too
  fragile (version-to-version schema risk, WAL-file corruption risk) for
  the payoff of automating a two-click, genuinely personal preference
  (which subtitle language you want).
- **The OpenSubtitles account itself** — has to be a real third-party
  account the user controls; the script only wires in credentials already
  provided, same boundary as everywhere else in this project (Claude
  doesn't create accounts on live external services on the user's behalf).
- **Homepage's cosmetic personalization** — weather location, background
  rotation, custom greeting text, and similar touches from decisions #9-10
  stay manual; the script's dashboard is a functional starting point, not
  a finished one.

**Execution model:** runs on the host (not as a Docker container), since it
needs `docker logs`/`docker compose up -d` and direct filesystem access to
`$DATA_ROOT` for both folder creation and reading each app's generated
config — a throwaway container would need the docker socket bind-mounted
just to shell back out to the CLI it's trying to avoid depending on.
`scripts/bootstrap.py` is self-bootstrapping: on first run it creates its
own throwaway virtualenv (`.bootstrap-venv`) and re-execs itself inside it,
so nothing installs globally and there's no separate shell wrapper to run
first — the same one command works on every platform (see the
cross-platform follow-up below).

**Follow-up, closed:** Ofelia's `rotate-background`/`poster-grid` scripts
(decision #10) originally lived only in `$DATA_ROOT/config/ofelia/`,
outside git — a fresh clone's Ofelia container had no script files to run.
Moved into `scripts/ofelia/` (git-tracked) with both job labels' `volume`
mount originally pointing at `${PWD}/scripts/ofelia` instead of
`${DATA_ROOT}/config/ofelia` (superseded by `${NEOFLIX_REPO_ROOT}` below).
`rotate-background.sh` also had the Jellyfin API key hardcoded inline
(predates decision #10's `JELLYFIN_API_KEY` externalization, and wasn't
caught at the time since it's a separate file from the compose label the
earlier fix touched) — fixed to read `JELLYFIN_URL`/`JELLYFIN_API_KEY` from
its job's `environment` label, same as `poster-grid` already did.

**Follow-up, closed — cross-platform support:** the original automation was
Mac-only in several places: `scripts/bootstrap.sh` (bash, Unix-only venv
paths), manual `id -u`/`id -g`/`readlink /etc/localtime` steps for
`PUID`/`PGID`/`TZ` in SETUP.md, and the `${PWD}` used in Ofelia's volume
labels above (a POSIX shell auto-export that PowerShell/cmd.exe don't set,
so it silently resolved blank there). Addressed together, since Windows
support was the point of all of them:

- `scripts/bootstrap.sh` deleted — `bootstrap.py` builds and re-execs into
  its own virtualenv itself (see above). One command everywhere:
  `python3 scripts/bootstrap.py` (macOS/Linux) / `python scripts/bootstrap.py`
  (Windows).
- `PUID`/`PGID`/`TZ` are auto-detected and written into `.env` (only if not
  already set, so a manual override survives re-runs) — `os.getuid()`/
  `os.getgid()` where available, `1000`/`1000` (linuxserver.io's own
  default) on Windows where the concept doesn't exist; timezone via the
  `tzlocal` package, which reads `/etc/localtime` on macOS/Linux and the
  registry on Windows.
- `${PWD}` in Ofelia's labels replaced with `${NEOFLIX_REPO_ROOT}`, a value
  `bootstrap.py` computes and writes into `.env` itself on every run —
  independent of shell/OS entirely, since Compose reads `.env` directly
  rather than inheriting shell-exported variables.
- A Docker-availability preflight check (fails fast with a clear message if
  Docker isn't on `PATH` or isn't running, rather than a raw traceback three
  steps later) and an interactive first-run wizard (prompts for `DATA_ROOT`
  and the admin login when `.env`/`credentials.env` aren't set up yet, or
  when `credentials.env` still has a placeholder password) were added at
  the same time — same goal of a frictionless first run, `input()`/
  `getpass` only, no new dependency.
- **Migration note for this repo's own already-running instance (and any
  other pre-existing deployment):** its `.env` predates
  `NEOFLIX_REPO_ROOT` — run `bootstrap.py` once after pulling this change,
  before the next plain `docker compose up -d`, or Ofelia's mounts break
  the same way the `${PWD}` bug did, just for an existing install instead
  of a fresh one.
- Full design/rollout discussion: `CROSS_PLATFORM_PLAN.md`.

---

### 14. Request lifecycle tracking (custom service) — DECIDED

**Problem:** Homepage's dashboard showed request/download status split
across three places — Jellyseerr's own Pending/Approved/Completed counts,
plus two `customapi` widgets bolted on showing Radarr's and Sonarr's raw
download queues (unlabeled rows, a hardcoded "No items found" empty state —
a genuine Homepage platform limitation, not a config gap). No single place
answered "where is the thing I requested, right now."

**Decision:** a small custom service, `scripts/lifecycle/` (Python/Flask),
that polls Jellyseerr, Radarr, and Sonarr every 30s, correlates each
request by `tmdbId`/`tvdbId`, and derives one lifecycle stage per item —
Requested → Searching → Downloading → Importing → Available (or Partially
Available for shows, or Declined) — cached in memory and served at
`GET /status` for Homepage's `customapi` widget to consume. Movies use
Radarr's `hasFile` flag; shows roll up to "X/Y episodes" from Sonarr's
episode-file-count statistics rather than showing per-episode rows, to keep
the card glanceable. When nothing's active, the service itself returns a
friendly placeholder item — this is what actually fixes the generic
"No items found" text, since Homepage's hardcoded string only appears when
the *mapped* list is empty, and this service's list never is.

**Follow-up, closed — library items added outside Jellyseerr:** the first
version only surfaced items with a Jellyseerr request behind them, so
anything added straight to Radarr/Sonarr (bypassing Jellyseerr entirely)
never appeared, even once fully downloaded — noticed immediately when
several already-available movies were missing from the card. Fixed by a
second pass in the same poll: any Radarr movie/Sonarr series *not* matched
to a Jellyseerr request gets appended once it has a file, as Available/
Partially Available. `WIDGET_LIMIT` raised from 6 to 12 accordingly, since
the card's job is now "everything in the library plus in-flight requests,"
not just active requests.

**Follow-up, closed — unbounded growth as the library grows:** the flat
alphabetical sort meant that once the library exceeded `WIDGET_LIMIT`, an
arbitrary (alphabetically-late) slice of "Available" titles silently
disappeared, and would keep changing unpredictably as more got added.
Split the output into two differently-behaved groups instead: **in-flight**
items (Requested/Searching/Downloading/Importing/Declined) stay uncapped
(this set is naturally small — a handful of active requests at most) and
sort by stage urgency, then oldest-request-first as a tiebreaker so a stuck
item surfaces. **Library** items (Available/Partially Available) are capped
to a small `RECENT_LIMIT` (default 5) and sorted by recency — Radarr's
`movieFile.dateAdded` for movies, the max `dateAdded` across a series'
episode files for shows (fetched per-series via Sonarr's `/episodefile?
seriesId=`, since that endpoint has no unfiltered "all files" mode) —
newest first, instead of alphabetically. This keeps the card's size stable
regardless of library size, and the same 5 "most recent" titles rotate out
predictably as new ones arrive, rather than an alphabetical cutoff quietly
hiding different things over time.

**Departure from decision #10, explicitly:** Ofelia's short-lived
`job-run` container model was chosen specifically to avoid a long-running
custom image in this repo. That model can't hold an HTTP endpoint open for
Homepage to poll — a job-run container exits after each run. This service
is therefore the first `build:`-based, continuously-running container in
`docker-compose.yml`, not an oversight of #10 but a case that genuinely
doesn't fit its "fire-and-forget job" shape.

**Conventions followed (matching the rest of the repo):** Python 3 +
`requests`, the only language/HTTP-client combo used anywhere else
(`scripts/bootstrap.py`); Flask added as the one new dependency, justified
because hand-rolling concurrent JSON HTTP on the stdlib is more code and
failure surface than one well-established micro-framework for a single
`/status` route. API keys follow the same path as `JELLYFIN_API_KEY`
(decision #7/#13): generated keys a compose `environment:` block needs at
container-creation time go in `.env`, written automatically by
`bootstrap.py`, never hardcoded into the committed compose file.

**Known limitations, accepted for this pass:**
- Radarr/Sonarr's own queue `timeleft` can lag behind qBittorrent's real
  progress (observed directly during earlier troubleshooting this
  session). V1 trusts the *arr apps' own reporting rather than adding
  qBittorrent as a 4th polled source; joining in qBittorrent by the queue
  entry's `downloadId` for ground-truth percentage is a natural V2 if this
  proves annoying in practice.
- No auth on the `/status` endpoint — acceptable since every other
  inter-service call in this stack is equally trusted-network-only
  (`neoflix-net`, no reverse proxy anywhere), flagged here rather than
  silently decided.

**Follow-up, open (2026-11-06) — `/status` no longer on the dashboard:**
the Jellyseerr card's lifecycle list was removed by choice (see #9's
personalizations), so nothing displays `/status` right now. The service
still polls every 30s, which keeps the list one widget-paste away if it
comes back. It also gained a second, unrelated route, `GET /host`, that
passes through the host-written LAN IP file for the Network card (#9).
If `/status` stays unused, the polling could be dropped and lifecycle
slimmed down to just `/host`.

---

### 15. Host disk space in Server Info (`scripts/diskstats/`) — REMOVED

Tried as a small custom service exposing host disk usage to a dedicated
Homepage widget; removed shortly after in favor of investigating
Homepage's native `glances` widget instead (real host CPU/memory/disk/
network in one place, rather than one custom endpoint per stat). Not part
of the stack.

---

### 16. Resource sizing (Docker Desktop VM + per-container limits) — DECIDED

**Context (measured 2026-11-09, stack idle 40h):** the host is an 8 GB M1.
Docker Desktop was on defaults (4 GB VM, all 8 CPUs, 1 GB swap) and the VM
was the largest process on the Mac (~4.1 GB resident) while macOS sat on
~8.7 GB of swap. Inside the VM, all 11 containers summed to only ~2.0 GB;
the rest was Linux page cache the VM never hands back to macOS. Load
average was ~0.4.

**Decision:**
- **Docker Desktop VM: 2.5 GB memory, 4 CPUs, 1 GB swap** (first cut was
  3 GB with all 11 services; lowered once the extras below were made
  opt-in). The core six use ~1.05 GB steady, leaving room for Jellyfin's
  observed ~0.9 GB peak during a transcode. Shrinking the containers alone
  frees nothing on the Mac: Apple's virtualization layer keeps the VM's
  full allocation resident (the guest fills spare RAM with page cache), so
  only the VM size itself matters. 4 CPUs caps how hard a transcode can
  starve macOS (house rule 7). Set in Docker Desktop → Settings →
  Resources; it is host config, not part of this repo.
- **Optional services behind the `extras` compose profile:** Jellyseerr,
  Lifecycle (reads Jellyseerr), Homepage, Ofelia (only feeds Homepage) and
  Uptime Kuma (~0.5 GB together, ~0.8 GB at peak). Plain
  `docker compose up -d` starts only the core six (Jellyfin, Radarr,
  Sonarr, Prowlarr, qBittorrent, Bazarr); adds go through Radarr/Sonarr
  directly (`neoflix-add`), so nothing core depends on the extras. Start
  them on demand with `docker compose up -d <name>` or
  `docker compose --profile extras up -d`; their config/data under
  `${DATA_ROOT}/config/` is untouched. Running all of them long-term needs
  the VM back at 3 GB. `scripts/bootstrap.py` passes `--profile extras` so
  first-time setup still configures everything.
- **`mem_limit` per service in `docker-compose.yml`**, at ~2× measured
  idle usage. These are ceilings, not reservations (their sum can exceed
  the VM), so one runaway container gets OOM-killed on its own instead of
  pushing the whole VM into OOM. Jellyfin gets the largest (1.5 GB) because
  ffmpeg runs inside its cgroup. Bazarr gets 512 MB rather than 384 MB
  because a manual ffsubsync run (see `neoflix-fix-subs`) loads audio into
  its process.
- **Jellyfin transcode throttling + segment deletion on** (Dashboard →
  Playback → Transcoding). Throttling keeps ffmpeg a few minutes ahead of
  the player instead of transcoding the whole file at full speed; segment
  deletion stops old segments piling up in `config/jellyfin/cache/transcodes`.
  No hardware acceleration is possible — Docker Desktop on macOS has no GPU
  passthrough.
- **Video transcoding disabled for the user account** (user policy
  `EnableVideoPlaybackTranscoding=false`; audio transcoding and remuxing
  stay on — both are cheap). Playback history (Sep 14–Nov 8) showed 57 of
  83 plays direct, the rest remux or audio-only, and exactly one real video
  transcode (Hero in Chrome, burning in a PGS-only subtitle). The intent:
  instead of re-encoding the library, fix the individual *file* that would
  need a video transcode (fetch a text subtitle for a PGS-only title,
  replace an AV1 release the TV can't decode).
  **Caveat, verified 2026-11-09 on Jellyfin 12.4.0: the flag is not
  enforced.** Jellyfin logs that it read the policy, but a temporary
  non-admin test user with the same policy still got a full H.264
  transcode (ffmpeg started and served segments). Treat the flag as a
  statement of intent only; it does not prevent transcodes. Real
  enforcement would need something else (e.g. the installed Transcode
  Guard plugin's limit) and has not been set up.
- **Media-fixing ffmpeg work runs on the host**, not via
  `docker exec jellyfin` (`neoflix-fix-audio`/`-subs`/`-seek` skills), so
  one-off analysis/encoding never competes with playback inside the VM.

**Revisit if:** a container keeps getting OOM-killed (`docker inspect
<name> --format '{{.State.OOMKilled}}'`) — raise that one limit; 4K or
high-bitrate transcodes stutter — raise VM CPUs to 6; the Mac still
swaps heavily — consider OrbStack, which returns unused VM memory to
macOS dynamically (deferred: it replaces Docker Desktop, which the rest of
these docs assume).

---

### 17. Host-side dashboard (`scripts/host/dashboard.sh`) — DECIDED

**Context:** after decision #16 moved Homepage/Ofelia/Lifecycle to the
opt-in `extras` profile, there was no everyday dashboard. What was
actually wanted from one: clickable links to each service plus the Mac's
LAN and public IP.

**Decision:** a single static `index.html` (in
`scripts/host/dashboard/`, no framework, no build) served from the Mac by
a launchd agent running `scripts/host/dashboard/serve.py` (macOS's
bundled Python, stdlib `http.server`) on **:3000**, Homepage's old port, so existing bookmarks keep working.
Homepage moved to :3002.
- **Data:** `lan-ip.sh` (decision #9) now also writes `public_ip` (from
  api.ipify.org, keeping the last known value on failure) and `updated` to
  the same `host.json`; the page reads it every 30s.
- **Links** are built from the hostname the page was opened at, so the
  same page works from the Mac, a phone or the TV with no hardcoded IP
  (the Mac's DHCP address has changed before).
- **Up/down dots** come from a `no-cors` fetch in the browser: they show
  reachability only, can't read status codes, and need no API keys.
- **Look (2026-11-09 revision):** styled after tvOS: a blurred, slowly
  drifting wall of the library's posters as the background, a rotating
  "Top Shelf" hero (backdrop + title logo, links to the title in
  Jellyfin), app-icon tiles that lift/tilt on hover or keyboard focus, and a
  Recently Added poster row. Service logos are bundled in
  `scripts/host/dashboard/icons/` (from homarr-labs/dashboard-icons).
- **Artwork** is cached on the host by `dashboard/refresh_media.py` (stdlib
  only) into `config/host/media/` with `media.json`: small posters,
  backdrops and logos (~1.4 MB for 10 movies). A third launchd agent,
  `com.neoflix.dashboard-media`, refreshes it at load and every 6h;
  `dashboard.sh refresh` forces it. The script reads the Jellyfin key from
  `.env` but never writes it into the served folder. Filenames include the
  image tag, so changed artwork gets a new URL and unchanged files aren't
  re-downloaded. Caching locally (rather than linking Jellyfin's image URLs)
  keeps the page working while Jellyfin is down.
- **Why `serve.py` instead of `python3 -m http.server`:** the stock server's
  listen backlog is 5. Measured: 63 of 111 parallel image requests got
  connection resets (broken images on page load); with a 128 backlog, 0 of
  111. It also sends `Cache-Control` (a day for media/icons, `no-cache` for
  the page and JSON) and drops per-request logging.

**Why on the host, not a container:** the LAN IP is only visible to a host
process (house rule 6), and `lan-ip.sh` already runs there, so the
container-side relay (Lifecycle's `/host`) becomes unnecessary. Cost
measured at ~20 MB and 0% idle CPU, against ~170 MB+ for Homepage + Ofelia
+ Lifecycle inside the VM.

**Constraints:**
- The server's root is **only** `${DATA_ROOT}/config/host`. Never widen it:
  sibling config folders hold API keys. `../` requests were tested and stay
  inside that folder.
- Python's `http.server` (which `serve.py` wraps) is not hardened:
  LAN-only, never port-forward it.
- The page holds no API keys, by design (anything on the LAN can load it).
- The page and icons are *copied* into the served folder; after editing
  them, run `scripts/host/dashboard.sh update`.

**Alternatives considered:** Caddy/nginx (a new dependency to serve two
files); macOS's built-in Apache (more config, no gain); opening the HTML
as a `file://` (only works on the Mac itself); keeping Homepage (~115 MB
plus Ofelia/Lifecycle just to feed it).

**Deferred:** deleting Homepage, Ofelia and Lifecycle from the compose
file entirely; they stay available under `extras` for now.

---

## Known Issues

### Tizen seek hangs from trailing silent-audio padding

**Symptom:** Jellyfin's Tizen app hung/froze on seek for one title
(Stargate EXTENDED (1994)) while every other title in the library seeked
fine, and playback itself (no seeking involved) was unaffected.

**Root cause:** the file's audio track had roughly five hours of pure
digital silence (~-91dB, the noise floor) appended after the real movie
audio ended — real content ran ~2h9m, but the audio track (and so the
container's overall reported duration) ran to ~7h13m. Jellyfin reports
the *longer* of the video/audio stream durations as the item's runtime,
so `RunTimeTicks` — and every client's seek-bar math built on it,
Tizen's included — was working off a duration roughly 3.3x too long.
Seeking anywhere past the real ~28% mark landed in a region with no
video data to find. The file's audio handler was tagged `GPAC ISO Audio
Handler`, suggesting the padding originated from whatever GPAC/MP4Box-
based tool packaged the release, though that's a correlation observed
once, not confirmed as the general cause.

**Fix:** a stream-copy `ffmpeg` remux explicitly capped at the video
stream's real duration (`-c copy -movflags +faststart -t <duration>`)
trims the padding losslessly. Notably, a plain `-c copy` remux with no
`-t` cutoff, and separately `-shortest`, both failed to trim it — the
padding is genuine per-frame audio data, not a stale header field.
Detailed step-by-step now lives in the `neoflix-fix-seek` skill, since
this is exactly the kind of thing worth checking first if it happens
again on another title.

---

## Roadmap

15 decisions made. Original scope (1-8) validated and in regular use;
dashboard, scheduled automation, uptime monitoring (9-11), automated
post-setup (13), and request-lifecycle tracking (14) added on top and
still active. Two additions were tried and later reverted (#12, #15) —
see those entries for the one-line reason each.

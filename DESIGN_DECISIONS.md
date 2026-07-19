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
error inside the VM. Impact at POC scale (1 movie + 1 episode) is
negligible — worst case, a few hundred MB copied instead of hardlinked.
Verify empirically once the POC runs (compare inode numbers or watch disk
usage after an import) rather than architecting around a maybe-problem now;
matters more once running a real library at scale.

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

### 6. Component scope for POC — DECIDED (revised)

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

**Git handling, now that the repo's future is known** (local during POC,
pushed to a **private** GitHub repo once validated):
- `.env` — gitignored. Not protecting a real secret today, but keeps
  machine-specific paths out of the committed file, and gives the eventual
  Mullvad/VPN credentials (decision #3's deferred item) an already-safe home
  when they're added — no rework needed when the repo goes to GitHub.
- **`.env.example`** — committed, placeholder values, documents the expected
  variables.
- `~/neoflix-data/` (media, downloads, all app config/databases) already
  lives entirely outside the `neoflix/` project folder (decision #1) — so it
  was never going to be swept up in a `git add` regardless of public/private.
- Private repo lowers the stakes vs. public, but doesn't change any of the
  above — same hygiene either way.

---

### 8. Execution details — DECIDED

**Decision:** see [docker-compose.yml](docker-compose.yml) — that file is the
source of truth for ports/mounts/env vars, not duplicated here (per the
LLD-lives-in-code call made earlier). A few choices worth flagging that
aren't self-evident from reading the YAML:

- **Image tags: `:latest`**, not pinned versions. Deliberate tradeoff — loses
  reproducibility, gains zero-maintenance for a POC that isn't running
  unattended long-term. Revisit if/when this moves to permanent hardware
  (decision worth re-litigating then, not now).
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

## Roadmap

All 8 decisions made. Next: bring the stack up and work through
USER_STORIES.md.

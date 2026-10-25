# neoflix — Goals

## What this is

A self-hosted local media server: automatically find and download movies/shows,
organize them into a library, and stream them to the local TV. Built on Docker,
following the established "*arr stack" community pattern (Jellyfin + Sonarr/Radarr +
Prowlarr + a download client) rather than inventing something new.

## Primary goals (now)

- **Serve media over LAN** via Jellyfin. Validated on iPhone (Jellyfin app
  or browser) and now on an actual TV too — Jellyfin's Tizen app, sideloaded
  via developer mode, running on a Samsung Smart TV on the same LAN.
- **Automate acquisition**: search → grab → download → organize into the library,
  without manual file wrangling.
- **Runs on an M1 Mac (Docker Desktop).** Started as a POC to validate the
  workflow end-to-end before committing to anything more permanent — that
  validation is done, and this is now the actual running setup. Setup tooling
  (`scripts/bootstrap.py`) also supports Windows and Linux (see
  [CROSS_PLATFORM_PLAN.md](CROSS_PLATFORM_PLAN.md)), but actual day-to-day
  running of the stack has only ever happened on this Mac.

## Secondary goals (later, not needed yet)

- **Remote/internet access** to the library from outside the LAN. Likely via
  Tailscale for personal devices; a reverse proxy (Caddy) only if/when sharing
  with people or TV apps that can't run Tailscale. Explicitly deferred — no design
  effort spent on this until it's actually wanted.
- Possibly move off the laptop onto always-on hardware (NAS / mini PC) — no
  longer gated on "does the workflow work" (it does), just on whether the
  investment is worth it for a laptop that has to stay awake to serve media.

## Non-goals

- Hardware-accelerated transcoding — M1 Docker Desktop can't pass through the
  video encoder, so software transcoding is accepted. Not a blocker; revisit
  only if it becomes a real bottleneck.
- High availability, multi-user account management, or anything beyond
  single-household use.
- Building custom tooling where a well-established community component
  (Sonarr/Radarr/Prowlarr/etc.) already solves the problem.

## Constraints

- **Machine**: MacBook (M1, Apple Silicon), Docker Desktop — the actual
  running instance, not a temporary stand-in. The bootstrap tooling is
  cross-platform (see above), but hasn't actually been run on Windows or
  Linux yet.
- **Disk**: shared with everything else on the laptop, not dedicated —
  observed free space has swung between ~15GB and ~55GB+ across a single
  session as unrelated apps/OS activity used or released space. Don't trust
  a point-in-time number from this doc; check `df -h` on `$DATA_ROOT` before
  any large acquisition. The library is kept intentionally modest — not a
  full personal collection — until/unless it moves to permanent storage.
- **Uptime**: it's a laptop, not a server. Containers pause on sleep. Fine
  for how this is actually used day to day; would need addressing before
  treating this as always-on infrastructure.

## Design status

All open questions resolved — see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
for the full log (storage layout, VPN, indexers, networking, component
scope, compose structure). The stack has been running and in active
day-to-day use (real acquisitions, real playback on iPhone and TV) well
past the original one-movie/one-episode validation pass — see
[USER_STORIES.md](USER_STORIES.md) for the original manual test plan this
grew out of.

## Original success criteria (met)

1. ✅ A title can be searched for and grabbed through Prowlarr/Sonarr/Radarr.
2. ✅ It downloads via the download client and lands in the Jellyfin library
   automatically (no manual file moves).
3. ✅ It plays back over LAN via the Jellyfin app/browser on iPhone, and via
   the Jellyfin Tizen app on a Samsung Smart TV.

# neoflix — Goals

## What this is

A self-hosted local media server: automatically find and download movies/shows,
organize them into a library, and stream them to the local TV. Built on Docker,
following the established "*arr stack" community pattern (Jellyfin + Sonarr/Radarr +
Prowlarr + a download client) rather than inventing something new.

## Primary goals (now)

- **Serve media over LAN** via Jellyfin. No TV yet — POC is validated on
  iPhone (Jellyfin app or browser); TV is the eventual target once available,
  same LAN setup applies either way.
- **Automate acquisition**: search → grab → download → organize into the library,
  without manual file wrangling.
- **Run as a POC on the M1 Mac (Docker Desktop)** first, to validate the workflow
  end-to-end before committing to any permanent hardware.

## Secondary goals (later, not needed for POC)

- **Remote/internet access** to the library from outside the LAN. Likely via
  Tailscale for personal devices; a reverse proxy (Caddy) only if/when sharing
  with people or TV apps that can't run Tailscale. Explicitly deferred — no design
  effort spent on this until the local setup is solid.
- Possibly move off the laptop onto always-on hardware (NAS / mini PC) once the
  POC validates the workflow.

## Non-goals

- Hardware-accelerated transcoding for the POC — M1 Docker Desktop can't pass
  through the video encoder, so software transcoding is accepted for now. Not a
  blocker; revisit only if it becomes a real bottleneck.
- High availability, multi-user account management, or anything beyond
  single-household use.
- Building custom tooling where a well-established community component
  (Sonarr/Radarr/Prowlarr/etc.) already solves the problem.

## Constraints

- **Machine**: MacBook (M1, Apple Silicon), Docker Desktop.
- **Disk**: ~48GB free at time of writing. POC should stay lightweight — a
  handful of test movies/episodes, not a full library — until moved to
  permanent storage.
- **Uptime**: it's a laptop, not a server. Containers pause on sleep. Fine for
  POC validation; not a target state.

## Design status

All open questions resolved — see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
for the full log (storage layout, VPN, indexers, networking, component
scope, compose structure). `docker-compose.yml` exists; next step is running
the stack and working through [USER_STORIES.md](USER_STORIES.md).

## Success criteria for the POC

1. A title can be searched for and grabbed through Prowlarr/Sonarr/Radarr.
2. It downloads via the download client and lands in the Jellyfin library
   automatically (no manual file moves).
3. It plays back on iPhone over LAN via the Jellyfin app/browser (TV once
   available — same setup, no changes expected).

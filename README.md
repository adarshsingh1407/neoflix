# neoflix

Self-hosted media server: automated movie/show acquisition + Jellyfin
streaming, running in Docker. Currently a POC on an M1 Mac, validated against
one movie and one show episode before any real usage.

## Status

**Design complete, POC not yet validated.** All 8 architecture decisions are
made and `docker-compose.yml` exists — see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
for the full log. Next step is bringing the stack up and working through
[USER_STORIES.md](USER_STORIES.md).

## Docs

Read in this order for full context:

1. [GOALS.md](GOALS.md) — what this is for, scope, non-goals, constraints.
2. [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) — every architecture decision
   made, in priority order, with rationale and what was deferred. The source
   of truth for "why is it built this way."
3. [HLD.md](HLD.md) — architecture and request-flow diagrams (mermaid);
   the visual shape of the system.
4. [USER_STORIES.md](USER_STORIES.md) — manual test plan for validating the
   POC once it's running, tagged by who performs each step (Claude vs. you).

## Stack

Six containers, one Docker bridge network, no VPN for the POC:

- **Jellyfin** — media server / playback
- **Radarr** — movie acquisition automation
- **Sonarr** — TV show acquisition automation
- **Prowlarr** — indexer management (public trackers)
- **qBittorrent** — download client
- **Jellyseerr** — unified search/request UI in front of Radarr/Sonarr/Jellyfin

Full rationale for each piece, and what was deliberately left out (VPN,
Lidarr/Readarr/comics, Bazarr, monitoring), is in DESIGN_DECISIONS.md.

## Data layout

Media, downloads, and app config all live outside this repo, at
`~/neoflix-data/` — see decision #1 in DESIGN_DECISIONS.md for the full
structure and why it's kept separate from the git-tracked project folder.

## Running it

```sh
cp .env.example .env   # already done for this machine; adjust PUID/PGID/TZ/DATA_ROOT if needed
docker compose up -d
```

Then work through [USER_STORIES.md](USER_STORIES.md) to wire up indexers,
download client, and Jellyseerr, and validate playback on iPhone.

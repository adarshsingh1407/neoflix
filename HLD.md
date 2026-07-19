# neoflix — High-Level Design

Visual companion to [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) — this file
shows the *shape* of the system; the decisions log has the *why* behind each
piece. Don't duplicate rationale here — if a diagram raises a "why is it
built this way" question, the answer belongs in DESIGN_DECISIONS.md, not
repeated in this file.

## Architecture

```mermaid
flowchart TB
    subgraph internet["Internet"]
        trackers[("Public Trackers")]
    end

    subgraph mac["M1 Mac - Docker Desktop"]
        subgraph net["Bridge network: neoflix-net"]
            prowlarr["Prowlarr<br>:9696"]
            radarr["Radarr<br>:7878"]
            sonarr["Sonarr<br>:8989"]
            qbit["qBittorrent<br>:8080"]
            jellyseerr["Jellyseerr<br>:5055"]
            jellyfin["Jellyfin<br>:8096"]
        end

        data[("neoflix-data<br>downloads, media, config")]
    end

    subgraph lan["Same LAN"]
        iphone["iPhone - Jellyfin app or Safari"]
        macbrowser["Mac Browser - admin setup"]
    end

    prowlarr -- search --> trackers
    prowlarr -- push indexers --> radarr
    prowlarr -- push indexers --> sonarr
    radarr -- send grab --> qbit
    sonarr -- send grab --> qbit
    qbit -- writes downloads --> data
    radarr -- import to media --> data
    sonarr -- import to media --> data
    jellyfin -- reads media --> data
    jellyseerr -- request movie --> radarr
    jellyseerr -- request show --> sonarr
    jellyseerr -- auth and library check --> jellyfin
    iphone -- stream --> jellyfin
    iphone -- search and request --> jellyseerr
    macbrowser -.admin UI.-> prowlarr
    macbrowser -.admin UI.-> radarr
    macbrowser -.admin UI.-> sonarr
    macbrowser -.admin UI.-> qbit
```

(Bridge network is decision #5, the shared `neoflix-data` mount is
decision #1 — see DESIGN_DECISIONS.md.)

Notes:
- Solid arrows are the runtime request/data flow; dotted arrows are one-off
  admin/setup access (story 1–4 in USER_STORIES.md), not something an end
  user touches.
- `qBittorrent`, `Radarr`, and `Sonarr` all mount the **same** `neoflix-data`
  parent (not separate volumes) — this is the hardlink requirement from
  decision #1, shown here as one shared volume rather than three.
- No VPN/gluetun node — decision #3 explicitly excludes it for the POC.
- Public trackers are reached directly from Prowlarr (search) and
  qBittorrent (swarm download) with no tunnel in between.

## Request → download → play flow

```mermaid
sequenceDiagram
    actor User as iPhone User
    participant JS as Jellyseerr
    participant Arr as Radarr or Sonarr
    participant PR as Prowlarr
    participant TR as Public Tracker
    participant QB as qBittorrent
    participant FS as neoflix-data filesystem
    participant JF as Jellyfin

    User->>JS: Search title, tap Request
    JS->>Arr: Create request via API
    Arr->>PR: Search indexers
    PR->>TR: Query
    TR-->>PR: Results, magnet or torrent
    PR-->>Arr: Best match
    Arr->>QB: Send grab
    QB->>TR: Download via swarm
    QB-->>FS: Save to downloads folder
    QB-->>Arr: Download complete
    Arr->>FS: Import to media folder, hardlink
    Note over JF,FS: Jellyfin scans media folder
    User->>JF: Open app, browse library
    JF-->>User: Title available
    User->>JF: Tap Play
    JF-->>User: Stream, direct play or transcode
```

(Import uses a hardlink per decision #1; Jellyfin's scan is story 6 in
USER_STORIES.md.)

Maps directly onto [USER_STORIES.md](USER_STORIES.md): the request half
(stories 2–4) is `Claude`-drivable via each app's API; the playback half
(stories 7–8) is the `[You]`-only iPhone portion, since it's the one leg no
API can stand in for.

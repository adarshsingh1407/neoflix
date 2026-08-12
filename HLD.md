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
            bazarr["Bazarr<br>:6767"]
            jfvue["Jellyfin Vue<br>:8090"]
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
    bazarr -- fetches subtitles for --> data
    jellyseerr -- request movie --> radarr
    jellyseerr -- request show --> sonarr
    jellyseerr -- auth and library check --> jellyfin
    jfvue -- pure client, no data of its own --> jellyfin
    iphone -- stream --> jellyfin
    iphone -- search and request --> jellyseerr
    macbrowser -.admin UI.-> prowlarr
    macbrowser -.admin UI.-> radarr
    macbrowser -.admin UI.-> sonarr
    macbrowser -.admin UI.-> qbit
    macbrowser -- browses --> jfvue
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
- Homepage, Ofelia, and Uptime Kuma (decisions 9-11) are left off this
  diagram — they don't participate in the request→download→play flow shown
  here, see the "Dashboard, scheduling & monitoring" diagram below instead.

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

## Dashboard, scheduling & monitoring

The optional add-ons from decisions 9-11. None of these sit in the
request→download→play path above — they're operational tooling layered on
top of it.

```mermaid
flowchart TB
    subgraph net["Bridge network: neoflix-net"]
        jellyfin["Jellyfin<br>:8096"]
        jellyseerr["Jellyseerr<br>:5055"]
        radarr["Radarr<br>:7878"]
        sonarr["Sonarr<br>:8989"]
        bazarr["Bazarr<br>:6767"]
        qbit["qBittorrent<br>:8080"]
        prowlarr["Prowlarr<br>:9696"]
        jfvue["Jellyfin Vue<br>:8090"]
        homepage["Homepage<br>:3000"]
        kuma["Uptime Kuma<br>:3001"]
        ofelia["Ofelia<br>(no port, label-driven)"]
    end

    docksock[("Docker socket<br>/var/run/docker.sock")]
    macbrowser["Mac Browser"]

    homepage -- reads status via each app's API key --> jellyfin
    homepage -- reads status via each app's API key --> jellyseerr
    homepage -- reads status via each app's API key --> radarr
    homepage -- reads status via each app's API key --> sonarr
    homepage -- reads status via each app's API key --> bazarr
    homepage -- reads status via each app's API key --> qbit
    homepage -- reads public status page --> kuma

    ofelia -- hourly: pick random backdrop --> jellyfin
    ofelia -- writes new background image --> homepage
    ofelia -- issues restart command --> docksock
    ofelia -- nightly: generate + push poster collage --> jellyfin
    docksock -.restarts to pick up new image.-> homepage

    kuma -- polls HTTP every 60s --> jellyfin
    kuma -- polls HTTP every 60s --> jellyseerr
    kuma -- polls HTTP every 60s --> radarr
    kuma -- polls HTTP every 60s --> sonarr
    kuma -- polls HTTP every 60s --> bazarr
    kuma -- polls HTTP every 60s --> qbit
    kuma -- polls HTTP every 60s --> prowlarr
    kuma -- polls HTTP every 60s --> jfvue

    macbrowser -- one dashboard for everything --> homepage
    macbrowser -- admin setup, once --> kuma
    macbrowser -- optional alt client --> jfvue
```

Notes:
- Ofelia has no web UI and isn't reachable itself — it acts entirely
  through the Docker socket (to restart Homepage after a background change)
  and the Jellyfin API (to read/write images). Configured via labels on its
  own container, not a separate config file.
- Homepage's API-key reads are one-way (dashboard pulling status) — it
  never writes to any of the apps it displays.
- Uptime Kuma polls independently of Homepage; Homepage only reads Kuma's
  public status page, it doesn't talk to the monitored apps on Kuma's
  behalf.

#!/usr/bin/env python3
"""Polls Jellyseerr/Radarr/Sonarr, correlates each request by tmdbId/tvdbId,
and serves the merged request-lifecycle status for Homepage's dashboard
widget. See DESIGN_DECISIONS.md decision #14 for the rationale."""

import logging
import os
import threading
import time

import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lifecycle")

JELLYSEERR_URL = os.environ.get("JELLYSEERR_URL", "http://jellyseerr:5055")
RADARR_URL = os.environ.get("RADARR_URL", "http://radarr:7878")
SONARR_URL = os.environ.get("SONARR_URL", "http://sonarr:8989")
JELLYSEERR_API_KEY = os.environ["JELLYSEERR_API_KEY"]
RADARR_API_KEY = os.environ["RADARR_API_KEY"]
SONARR_API_KEY = os.environ["SONARR_API_KEY"]

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
TRACK_LIMIT = int(os.environ.get("TRACK_LIMIT", "20"))
RECENT_LIMIT = int(os.environ.get("RECENT_LIMIT", "5"))
WIDGET_LIMIT = int(os.environ.get("WIDGET_LIMIT", "12"))
REQUEST_TIMEOUT = 10

LIBRARY_STAGES = {"Available", "Partially Available"}

STAGE_LABELS = {
    "Requested": "Requested",
    "Searching": "Searching for a release",
    "Downloading": "Downloading",
    "Importing": "Importing",
    "Available": "Available now",
    "Partially Available": "Partially available",
    "Declined": "Declined",
}
STAGE_PRIORITY = {
    "Downloading": 0,
    "Importing": 1,
    "Searching": 2,
    "Requested": 3,
    "Partially Available": 4,
    "Available": 5,
    "Declined": 6,
}

_cache_lock = threading.Lock()
_cache = {"items": []}


def format_timeleft(raw):
    if not raw:
        return None
    try:
        h, m, s = (int(part) for part in raw.split(":"))
    except (ValueError, AttributeError):
        return raw
    if h:
        return f"{h}h {m}m left"
    if m:
        return f"{m}m left"
    return f"{s}s left"


def get_json(url, headers, params=None):
    r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_jellyseerr_requests():
    headers = {"X-Api-Key": JELLYSEERR_API_KEY}
    data = get_json(
        f"{JELLYSEERR_URL}/api/v1/request",
        headers,
        params={"take": TRACK_LIMIT, "filter": "all", "sort": "added"},
    )
    return data.get("results", [])


def fetch_fallback_title(media):
    headers = {"X-Api-Key": JELLYSEERR_API_KEY}
    try:
        if media["mediaType"] == "movie":
            data = get_json(f"{JELLYSEERR_URL}/api/v1/movie/{media['tmdbId']}", headers)
            return data.get("title", "Unknown title")
        data = get_json(f"{JELLYSEERR_URL}/api/v1/tv/{media['tvdbId']}", headers)
        return data.get("name", "Unknown title")
    except requests.RequestException:
        return "Unknown title"


def fetch_radarr_state():
    headers = {"X-Api-Key": RADARR_API_KEY}
    movies = get_json(f"{RADARR_URL}/api/v3/movie", headers)
    queue = get_json(f"{RADARR_URL}/api/v3/queue", headers, params={"pageSize": 200})
    movies_by_tmdb = {m["tmdbId"]: m for m in movies}
    queue_by_movie_id = {}
    for record in queue.get("records", []):
        movie_id = record.get("movieId")
        if movie_id is not None and movie_id not in queue_by_movie_id:
            queue_by_movie_id[movie_id] = record
    return movies_by_tmdb, queue_by_movie_id


def fetch_sonarr_state():
    headers = {"X-Api-Key": SONARR_API_KEY}
    series = get_json(f"{SONARR_URL}/api/v3/series", headers)
    queue = get_json(f"{SONARR_URL}/api/v3/queue", headers, params={"pageSize": 200})
    series_by_tvdb = {s["tvdbId"]: s for s in series}
    queue_by_series_id = {}
    for record in queue.get("records", []):
        series_id = record.get("seriesId")
        if series_id is not None and series_id not in queue_by_series_id:
            queue_by_series_id[series_id] = record
    return series_by_tvdb, queue_by_series_id


def fetch_series_latest_file_date(series_id, cache):
    # Sonarr has no "all episode files" endpoint -- seriesId is required --
    # so this is called lazily per series that actually needs a recency
    # date, and memoized for the rest of this poll.
    if series_id in cache:
        return cache[series_id]
    headers = {"X-Api-Key": SONARR_API_KEY}
    files = get_json(f"{SONARR_URL}/api/v3/episodefile", headers, params={"seriesId": series_id})
    dates = [f["dateAdded"] for f in files if f.get("dateAdded")]
    cache[series_id] = max(dates) if dates else ""
    return cache[series_id]


def resolve_movie(media, movies_by_tmdb, queue_by_movie_id):
    movie = movies_by_tmdb.get(media.get("tmdbId"))
    if not movie:
        return fetch_fallback_title(media), "Requested", None
    title = movie["title"]
    if movie.get("hasFile"):
        return title, "Available", None
    record = queue_by_movie_id.get(movie["id"])
    if not record:
        return title, "Searching", None
    status = (record.get("trackedDownloadStatus") or record.get("status") or "").lower()
    if "completed" in status or "import" in status:
        return title, "Importing", None
    return title, "Downloading", format_timeleft(record.get("timeleft"))


def resolve_series(media, series_by_tvdb, queue_by_series_id):
    series = series_by_tvdb.get(media.get("tvdbId"))
    if not series:
        return fetch_fallback_title(media), "Requested", None
    title = series["title"]
    stats = series.get("statistics", {})
    file_count = stats.get("episodeFileCount", 0)
    episode_count = stats.get("episodeCount", 0)
    if episode_count and file_count == episode_count:
        return title, "Available", None
    record = queue_by_series_id.get(series["id"])
    if record:
        return title, "Downloading", format_timeleft(record.get("timeleft"))
    if file_count:
        return title, "Partially Available", f"{file_count}/{episode_count} episodes"
    return title, "Searching", None


def poll_once():
    requests_list = fetch_jellyseerr_requests()
    movies_by_tmdb, queue_by_movie_id = fetch_radarr_state()
    series_by_tvdb, queue_by_series_id = fetch_sonarr_state()
    series_file_date_cache = {}

    # Two buckets with different display rules:
    #  - in_flight: nothing available yet, naturally small and self-limiting
    #    (rare to have more than a couple requests active at once) -- left
    #    uncapped, ordered by stage urgency then by how long it's been
    #    waiting (oldest first, to surface anything stuck).
    #  - library: already has content. This is the part that grows with
    #    your library over time, so it's capped to a small "recently added"
    #    window and ordered by recency instead of alphabetically -- an
    #    alphabetical cut silently drops different titles as the library
    #    grows; a recency cut always drops the same (oldest, least
    #    interesting) ones.
    in_flight = []
    library = []
    requested_tmdb_ids = set()
    requested_tvdb_ids = set()

    for result in requests_list:
        media = result.get("media") or {}
        media_type = media.get("mediaType")
        icon = "🎬" if media_type == "movie" else "📺"

        if media_type == "movie":
            requested_tmdb_ids.add(media.get("tmdbId"))
        elif media_type == "tv":
            requested_tvdb_ids.add(media.get("tvdbId"))

        if result.get("status") == 3:
            title = fetch_fallback_title(media)
            stage, detail = "Declined", None
        elif media_type == "movie":
            title, stage, detail = resolve_movie(media, movies_by_tmdb, queue_by_movie_id)
        elif media_type == "tv":
            title, stage, detail = resolve_series(media, series_by_tvdb, queue_by_series_id)
        else:
            continue

        label = STAGE_LABELS[stage] + (f" · {detail}" if detail else "")
        entry = {"name": f"{icon} {title}", "label": label}

        if stage in LIBRARY_STAGES:
            if media_type == "movie":
                movie = movies_by_tmdb.get(media.get("tmdbId")) or {}
                recency = (movie.get("movieFile") or {}).get("dateAdded", "")
            else:
                series = series_by_tvdb.get(media.get("tvdbId")) or {}
                recency = fetch_series_latest_file_date(series.get("id"), series_file_date_cache) if series.get("id") else ""
            library.append((recency, entry))
        else:
            in_flight.append((STAGE_PRIORITY[stage], result.get("createdAt") or "", entry))

    # Library items added straight to Radarr/Sonarr (not via a Jellyseerr
    # request) have no request to correlate to -- surface them once they
    # actually have a file, so "available" reflects the whole library, not
    # just what was requested through Jellyseerr.
    for tmdb_id, movie in movies_by_tmdb.items():
        if tmdb_id in requested_tmdb_ids or not movie.get("hasFile"):
            continue
        recency = (movie.get("movieFile") or {}).get("dateAdded", "")
        entry = {"name": f"🎬 {movie['title']}", "label": STAGE_LABELS["Available"]}
        library.append((recency, entry))

    for tvdb_id, series in series_by_tvdb.items():
        if tvdb_id in requested_tvdb_ids:
            continue
        stats = series.get("statistics", {})
        file_count = stats.get("episodeFileCount", 0)
        episode_count = stats.get("episodeCount", 0)
        if not file_count:
            continue
        if episode_count and file_count == episode_count:
            stage, detail = "Available", None
        else:
            stage, detail = "Partially Available", f"{file_count}/{episode_count} episodes"
        label = STAGE_LABELS[stage] + (f" · {detail}" if detail else "")
        recency = fetch_series_latest_file_date(series["id"], series_file_date_cache)
        library.append((recency, {"name": f"📺 {series['title']}", "label": label}))

    in_flight.sort(key=lambda t: (t[0], t[1]))
    library.sort(key=lambda t: t[0], reverse=True)

    items = [entry for _, _, entry in in_flight] + [entry for _, entry in library[:RECENT_LIMIT]]
    items = items[:WIDGET_LIMIT]

    if not items:
        items = [{"name": "✅ All caught up", "label": "Nothing in progress"}]

    return items


def poll_loop():
    while True:
        try:
            items = poll_once()
            with _cache_lock:
                _cache["items"] = items
            log.info("poll ok: %d item(s)", len(items))
        except requests.RequestException as exc:
            log.warning("poll failed, serving last-known state: %s", exc)
        time.sleep(POLL_INTERVAL_SECONDS)


app = Flask(__name__)


@app.route("/status")
def status():
    with _cache_lock:
        items = list(_cache["items"])
    return jsonify({"items": items})


if __name__ == "__main__":
    threading.Thread(target=poll_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8100, threaded=True)

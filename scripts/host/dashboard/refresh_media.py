#!/usr/bin/env python3
"""Cache movie and series artwork from Jellyfin for the host dashboard.

Writes <serve_dir>/media/ with small posters, backdrops and title logos plus
media.json describing them. The page reads only these files, so it needs no
API key and still has artwork when Jellyfin is down. The API key is read from
the repo's .env at run time and never written into the served folder.

Python standard library only (runs under macOS's bundled /usr/bin/python3).
Invoked by scripts/host/dashboard.sh (`refresh`, and the launchd agent).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
JELLYFIN = "http://localhost:8096"
TIMEOUT = 20

# Sizes chosen for a blurred background and a ~1200px-wide hero, not detail.
POSTER = {"maxHeight": 360, "quality": 75}
BACKDROP = {"maxWidth": 1280, "quality": 75}
LOGO = {"maxWidth": 600}


def read_env():
    env = {}
    with open(os.path.join(REPO_ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_json(path, key, params):
    url = f"{JELLYFIN}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f'MediaBrowser Token="{key}"'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def download(item_id, image_type, params, dest):
    # Image endpoints don't need auth; write-then-rename so the page never
    # loads a half-written file.
    url = f"{JELLYFIN}/Items/{item_id}/Images/{image_type}?{urllib.parse.urlencode(params)}"
    tmp = dest + ".tmp"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r, open(tmp, "wb") as f:
        f.write(r.read())
    os.replace(tmp, dest)


def main(serve_dir):
    env = read_env()
    key = env.get("JELLYFIN_API_KEY")
    if not key:
        sys.exit("JELLYFIN_API_KEY missing from .env")

    out = os.path.join(serve_dir, "media")
    os.makedirs(out, exist_ok=True)

    # Movies plus series (the Shows and Anime libraries are both Series).
    items = get_json("/Items", key, {
        "IncludeItemTypes": "Movie,Series",
        "Recursive": "true",
        "Fields": "Genres,Overview",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
    })["Items"]

    titles, keep = [], {"media.json"}
    for it in items:
        tags = it.get("ImageTags", {})
        if "Primary" not in tags:
            continue  # new items can lack art until a refresh (house rule 5)
        iid = it["Id"]
        is_series = it.get("Type") == "Series"
        episodes = None
        if is_series:
            # Only series with episode files actually on disk: Jellyfin can
            # list metadata-only "missing" episodes for a series.
            episodes = get_json("/Items", key, {
                "ParentId": iid, "IncludeItemTypes": "Episode", "Recursive": "true",
                "IsMissing": "false", "Limit": 0,
            })["TotalRecordCount"]
            if not episodes:
                continue
        entry = {
            "id": iid,
            "type": "series" if is_series else "movie",
            "name": it.get("Name", ""),
            "year": it.get("ProductionYear"),
            "genres": (it.get("Genres") or [])[:3],
            "rating": it.get("OfficialRating"),
            # A series' RunTimeTicks is one episode's length; show episodes instead.
            "minutes": round(it["RunTimeTicks"] / 600_000_000) if it.get("RunTimeTicks") and not is_series else None,
            "episodes": episodes,
            "overview": (it.get("Overview") or "")[:280],
        }
        # Filenames carry the image tag, so changed artwork gets a new URL
        # (no stale browser cache) and unchanged artwork isn't re-downloaded.
        wanted = [("poster", "Primary", POSTER, ".jpg", tags.get("Primary"))]
        if it.get("BackdropImageTags"):
            wanted.append(("backdrop", "Backdrop", BACKDROP, ".jpg", it["BackdropImageTags"][0]))
        if "Logo" in tags:
            wanted.append(("logo", "Logo", LOGO, ".png", tags["Logo"]))
        for field, image_type, params, ext, tag in wanted:
            name = f"{iid}-{field}-{tag[:8]}{ext}"
            path = os.path.join(out, name)
            try:
                if not os.path.exists(path):
                    download(iid, image_type, params, path)
                entry[field] = f"media/{name}"
                keep.add(name)
            except OSError as e:
                print(f"skip {entry['name']} {field}: {e}", file=sys.stderr)
        titles.append(entry)

    tmp = os.path.join(out, "media.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"titles": titles}, f)
    os.replace(tmp, os.path.join(out, "media.json"))

    # Drop artwork for titles that left the library (only files in media/).
    for name in os.listdir(out):
        if name not in keep and not name.endswith(".tmp"):
            os.remove(os.path.join(out, name))

    print(f"{len(titles)} titles cached in {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: refresh_media.py <serve_dir>")
    main(sys.argv[1])

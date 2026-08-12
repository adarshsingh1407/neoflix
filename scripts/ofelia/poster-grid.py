#!/usr/bin/env python3
import base64
import io
import os

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

JELLYFIN_URL = os.environ["JELLYFIN_URL"]
API_KEY = os.environ["JELLYFIN_API_KEY"]
HEADERS = {"X-Emby-Token": API_KEY}

CANVAS_W, CANVAS_H = 1920, 1080
MAX_TILES = 6
FONT_PATH = "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"

# (cols, rows) for each possible unique-poster count, chosen so tiles stay
# close to portrait poster proportions (avoids repeats by shrinking the grid
# instead of cycling back through the same posters).
GRID_LAYOUTS = {
    1: (1, 1),
    2: (2, 1),
    3: (3, 1),
    4: (2, 2),
    5: (5, 1),
    6: (3, 2),
}

LIBRARIES = ["Movies", "Shows", "Anime", "Music"]


def get_library_id(name):
    r = requests.get(f"{JELLYFIN_URL}/Library/VirtualFolders", headers=HEADERS)
    r.raise_for_status()
    for lib in r.json():
        if lib["Name"] == name:
            return lib["ItemId"]
    return None


def get_poster_item_ids(library_id, count):
    params = {
        "ParentId": library_id,
        "Recursive": "false",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
        "Limit": count,
    }
    r = requests.get(f"{JELLYFIN_URL}/Items", headers=HEADERS, params=params)
    r.raise_for_status()
    return [item["Id"] for item in r.json()["Items"]]


def fetch_poster(item_id):
    r = requests.get(f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary", headers=HEADERS)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def build_grid(item_ids):
    cols, rows = GRID_LAYOUTS[len(item_ids)]
    tile_w, tile_h = CANVAS_W // cols, CANVAS_H // rows
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H))
    for i, item_id in enumerate(item_ids):
        poster = fetch_poster(item_id)
        tile = ImageOps.fit(poster, (tile_w, tile_h), Image.LANCZOS, centering=(0.5, 0.5))
        x = (i % cols) * tile_w
        y = (i // cols) * tile_h
        canvas.paste(tile, (x, y))
    return canvas


def add_label(canvas, text):
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.truetype(FONT_PATH, 48)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 20
    badge_w, badge_h = tw + pad * 2, th + pad * 2
    x0, y0 = 30, CANVAS_H - badge_h - 30
    draw.rounded_rectangle(
        [x0, y0, x0 + badge_w, y0 + badge_h], radius=12, fill=(0, 0, 0, 160)
    )
    draw.text((x0 + pad, y0 + pad - bbox[1]), text, font=font, fill=(255, 255, 255, 255))


def upload_poster(library_id, canvas):
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    # Jellyfin's image-upload endpoint base64-decodes the request body server-side
    # despite its OpenAPI spec claiming raw binary; confirmed against the live server.
    encoded = base64.b64encode(buf.getvalue())
    r = requests.post(
        f"{JELLYFIN_URL}/Items/{library_id}/Images/Primary",
        headers={**HEADERS, "Content-Type": "image/png"},
        data=encoded,
    )
    r.raise_for_status()


def main():
    for name in LIBRARIES:
        lib_id = get_library_id(name)
        if not lib_id:
            print(f"Library '{name}' not found, skipping")
            continue

        item_ids = get_poster_item_ids(lib_id, MAX_TILES)
        if not item_ids:
            print(f"No items found in '{name}', skipping")
            continue

        canvas = build_grid(item_ids)
        add_label(canvas, name.upper())
        upload_poster(lib_id, canvas)
        print(f"Updated poster for '{name}' ({len(item_ids)} unique posters)")


if __name__ == "__main__":
    main()

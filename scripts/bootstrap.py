#!/usr/bin/env python3
"""
One-time post-setup automation for neoflix (see SETUP.md step 6 and
DESIGN_DECISIONS.md decision #13). Run via scripts/bootstrap.sh, not
directly -- that wrapper sets up a venv with the right dependencies first.

Replaces the manual "create an account / paste an API key into every app"
steps with scripted calls to each app's own API, using API keys read
straight off disk. The only manual input is credentials.env.

Safe to re-run: every step checks existing state before changing anything.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

WARNINGS = []


def log(msg):
    print(f"[bootstrap] {msg}")


def warn(msg):
    print(f"[bootstrap] WARNING: {msg}")
    WARNINGS.append(msg)


def load_env_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


ENV = {**load_env_file(REPO_ROOT / ".env"), **load_env_file(REPO_ROOT / "credentials.env")}

DATA_ROOT = Path(ENV["DATA_ROOT"]).expanduser()
ADMIN_USERNAME = ENV.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = ENV.get("ADMIN_PASSWORD", "")
OPENSUBTITLES_USERNAME = ENV.get("OPENSUBTITLES_USERNAME", "")
OPENSUBTITLES_PASSWORD = ENV.get("OPENSUBTITLES_PASSWORD", "")

if not ADMIN_PASSWORD or ADMIN_PASSWORD == "changeme":
    sys.exit("credentials.env: set a real ADMIN_PASSWORD before running bootstrap.")

JELLYFIN_CLIENT_HEADER = (
    'MediaBrowser Client="neoflix-bootstrap", Device="bootstrap-script", '
    'DeviceId="neoflix-bootstrap-001", Version="1.0.0"'
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def wait_for_http(url, timeout=120, **kwargs):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5, **kwargs)
            if r.status_code < 500:
                return r
        except requests.RequestException as e:
            last_error = e
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def wait_for_file_containing(path, pattern, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            text = path.read_text(errors="ignore")
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {path} to contain {pattern}")


# ---------------------------------------------------------------------------
# Step 1: folders (replaces SETUP.md step 4)
# ---------------------------------------------------------------------------

def create_folders():
    log(f"Creating data folders under {DATA_ROOT}")
    for d in ("movies", "tv", "music", "books", "audiobooks", "comics"):
        (DATA_ROOT / "downloads" / d).mkdir(parents=True, exist_ok=True)
    for d in ("movies", "tv", "anime", "music", "books", "audiobooks", "comics"):
        (DATA_ROOT / "media" / d).mkdir(parents=True, exist_ok=True)
    for app in (
        "jellyfin", "sonarr", "radarr", "prowlarr", "qbittorrent",
        "jellyseerr", "bazarr", "homepage", "uptime-kuma",
    ):
        (DATA_ROOT / "config" / app).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Step 2: bring the stack up, then read each *arr app's self-generated key
# ---------------------------------------------------------------------------

def docker_compose_up():
    log("Starting the stack (docker compose up -d)")
    r = run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT)
    if r.returncode != 0:
        sys.exit(f"docker compose up -d failed:\n{r.stdout}\n{r.stderr}")


def read_arr_api_key(app):
    config_xml = DATA_ROOT / "config" / app / "config.xml"
    log(f"Waiting for {app} to generate its API key...")
    return wait_for_file_containing(config_xml, r"<ApiKey>([^<]+)</ApiKey>")


# ---------------------------------------------------------------------------
# qBittorrent: temp password -> permanent credentials + save path
# ---------------------------------------------------------------------------

def qbittorrent_logged_in(session):
    # Success response has varied across versions ("200 Ok." vs "204" with an
    # empty body) -- the one constant is that a session cookie gets set.
    return any("SID" in c.name for c in session.cookies)


def setup_qbittorrent():
    log("Configuring qBittorrent")
    wait_for_http("http://localhost:8080")

    log_result = run(["docker", "logs", "qbittorrent"])
    logs = log_result.stdout + log_result.stderr
    # docker logs returns the full history across restarts -- take the last
    # match, since an earlier restart's temp password is no longer valid.
    matches = re.findall(r"temporary password.*?:\s*(\S+)", logs, re.IGNORECASE)

    session = requests.Session()
    if matches:
        temp_password = matches[-1]
        session.post(
            "http://localhost:8080/api/v2/auth/login",
            data={"username": "admin", "password": temp_password},
        )
        if not qbittorrent_logged_in(session):
            warn("qBittorrent: login with temporary password failed, trying admin credentials instead")
            matches = []

    if not matches:
        session.post(
            "http://localhost:8080/api/v2/auth/login",
            data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        if not qbittorrent_logged_in(session):
            warn("qBittorrent: could not log in with temp password or admin credentials -- skipping")
            return

    prefs = {
        "web_ui_username": ADMIN_USERNAME,
        "web_ui_password": ADMIN_PASSWORD,
        "save_path": "/data/downloads",
    }
    r = session.post(
        "http://localhost:8080/api/v2/app/setPreferences",
        data={"json": json.dumps(prefs)},
    )
    if r.status_code == 200:
        log("qBittorrent: credentials and save path set")
    else:
        warn(f"qBittorrent: setPreferences failed ({r.status_code})")


# ---------------------------------------------------------------------------
# Prowlarr: public indexers + Radarr/Sonarr app links
# ---------------------------------------------------------------------------

def setup_prowlarr(prowlarr_key, radarr_key, sonarr_key):
    log("Configuring Prowlarr")
    wait_for_http("http://localhost:9696")
    base = "http://localhost:9696/api/v1"
    headers = {"X-Api-Key": prowlarr_key}

    existing_indexers = {i["name"] for i in requests.get(f"{base}/indexer", headers=headers).json()}
    schema = requests.get(f"{base}/indexer/schema", headers=headers).json()
    # Knaben and YTS both work without FlareSolverr (not part of this stack);
    # 1337x/EZTV need it to get past Cloudflare and would just fail here.
    for wanted in ("Knaben", "YTS"):
        if wanted in existing_indexers:
            continue
        tmpl = next((s for s in schema if s["name"] == wanted), None)
        if not tmpl:
            warn(f"Prowlarr: indexer '{wanted}' not found in schema, skipping")
            continue
        tmpl["enable"] = True
        tmpl["appProfileId"] = 1  # the "Standard" profile Prowlarr ships by default
        r = requests.post(f"{base}/indexer", headers=headers, json=tmpl)
        if r.ok:
            log(f"Prowlarr: added indexer {wanted}")
        else:
            warn(f"Prowlarr: failed to add indexer {wanted}: {r.status_code} {r.text[:200]}")

    existing_apps = {a["name"] for a in requests.get(f"{base}/applications", headers=headers).json()}
    app_schema = requests.get(f"{base}/applications/schema", headers=headers).json()
    for name, base_url, api_key in (
        ("Radarr", "http://radarr:7878", radarr_key),
        ("Sonarr", "http://sonarr:8989", sonarr_key),
    ):
        if name in existing_apps:
            continue
        tmpl = next((s for s in app_schema if s["implementation"] == name), None)
        if not tmpl:
            warn(f"Prowlarr: application template '{name}' not found, skipping")
            continue
        tmpl["name"] = name
        tmpl["syncLevel"] = "fullSync"
        for f in tmpl["fields"]:
            if f["name"] == "baseUrl":
                f["value"] = base_url
            elif f["name"] == "prowlarrUrl":
                f["value"] = "http://prowlarr:9696"
            elif f["name"] == "apiKey":
                f["value"] = api_key
        r = requests.post(f"{base}/applications", headers=headers, json=tmpl)
        if r.ok:
            log(f"Prowlarr: linked {name}")
        else:
            warn(f"Prowlarr: failed to link {name}: {r.status_code} {r.text[:200]}")


# ---------------------------------------------------------------------------
# Radarr / Sonarr: download client, root folder, "Any" quality profile
# ---------------------------------------------------------------------------

def setup_arr_app(name, port, api_key, root_folder):
    log(f"Configuring {name}")
    wait_for_http(f"http://localhost:{port}")
    base = f"http://localhost:{port}/api/v3"
    headers = {"X-Api-Key": api_key}

    clients = requests.get(f"{base}/downloadclient", headers=headers).json()
    if not any(c["name"] == "qBittorrent" for c in clients):
        schema = requests.get(f"{base}/downloadclient/schema", headers=headers).json()
        tmpl = next(s for s in schema if s["implementation"] == "QBittorrent")
        tmpl["name"] = "qBittorrent"
        tmpl["enable"] = True
        for f in tmpl["fields"]:
            if f["name"] == "host":
                f["value"] = "qbittorrent"
            elif f["name"] == "port":
                f["value"] = 8080
            elif f["name"] == "username":
                f["value"] = ADMIN_USERNAME
            elif f["name"] == "password":
                f["value"] = ADMIN_PASSWORD
        r = requests.post(f"{base}/downloadclient", headers=headers, json=tmpl)
        if r.ok:
            log(f"{name}: added qBittorrent as download client")
        else:
            warn(f"{name}: failed to add download client: {r.status_code} {r.text[:200]}")

    folders = requests.get(f"{base}/rootfolder", headers=headers).json()
    if not any(f["path"] == root_folder for f in folders):
        r = requests.post(f"{base}/rootfolder", headers=headers, json={"path": root_folder})
        if r.ok:
            log(f"{name}: added root folder {root_folder}")
        else:
            warn(f"{name}: failed to add root folder: {r.status_code} {r.text[:200]}")

    profiles = requests.get(f"{base}/qualityprofile", headers=headers).json()
    any_profile = next((p for p in profiles if p["name"] == "Any"), None)
    if not any_profile:
        schema = requests.get(f"{base}/qualityprofile/schema", headers=headers).json()
        schema["name"] = "Any"
        schema["upgradeAllowed"] = False
        cutoff = None
        for item in schema["items"]:
            quality_name = item.get("quality", {}).get("name")
            item["allowed"] = quality_name not in ("Unknown", "Raw-HD")
            if item["allowed"] and cutoff is None:
                cutoff = item.get("quality", {}).get("id")
        schema["cutoff"] = cutoff
        r = requests.post(f"{base}/qualityprofile", headers=headers, json=schema)
        if r.ok:
            any_profile = r.json()
            log(f"{name}: created 'Any' quality profile")
        else:
            warn(f"{name}: failed to create quality profile: {r.status_code} {r.text[:200]}")

    return any_profile["id"] if any_profile else None


# ---------------------------------------------------------------------------
# Bazarr: patch its self-generated config.yaml (no REST API for settings)
# ---------------------------------------------------------------------------

def setup_bazarr(radarr_key, sonarr_key):
    log("Configuring Bazarr")
    config_path = DATA_ROOT / "config" / "bazarr" / "config" / "config.yaml"
    deadline = time.time() + 120
    while not config_path.exists() and time.time() < deadline:
        time.sleep(2)
    if not config_path.exists():
        warn("Bazarr: config.yaml never appeared, skipping")
        return

    # Bazarr flushes its own in-memory config back to config.yaml on
    # shutdown -- editing the file and then `restart`ing races that flush
    # and our edit silently gets clobbered back to blank. Stopping first
    # means there's no running process left to overwrite what we write.
    run(["docker", "stop", "bazarr"])

    data = yaml.safe_load(config_path.read_text()) or {}
    data.setdefault("radarr", {})["ip"] = "radarr"
    data["radarr"]["port"] = 7878
    data["radarr"]["apikey"] = radarr_key
    data.setdefault("sonarr", {})["ip"] = "sonarr"
    data["sonarr"]["port"] = 8989
    data["sonarr"]["apikey"] = sonarr_key
    data.setdefault("general", {})["use_radarr"] = True
    data["general"]["use_sonarr"] = True

    if OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD:
        data.setdefault("opensubtitlescom", {})["username"] = OPENSUBTITLES_USERNAME
        data["opensubtitlescom"]["password"] = OPENSUBTITLES_PASSWORD
        providers = set(data["general"].get("enabled_providers") or [])
        providers.add("opensubtitlescom")
        data["general"]["enabled_providers"] = sorted(providers)
    else:
        log("Bazarr: no OpenSubtitles credentials in credentials.env, leaving subtitle provider unset")

    config_path.write_text(yaml.safe_dump(data, sort_keys=True))
    run(["docker", "start", "bazarr"])
    log("Bazarr: connected to Radarr/Sonarr, restarted to apply")
    warn(
        "Bazarr: language profile (Settings > Languages) isn't automated -- it's "
        "stored in Bazarr's own database, not a file or documented API. Pick your "
        "subtitle language(s) there once, by hand."
    )

    # auth.apikey may still be blank at this point -- Bazarr only generates
    # it while booting, and if config.yaml was read before that finished the
    # first time, the value we just wrote back is the pre-generation blank.
    # Re-read after the restart rather than trusting the earlier snapshot.
    deadline = time.time() + 60
    while time.time() < deadline:
        reloaded = yaml.safe_load(config_path.read_text()) or {}
        key = reloaded.get("auth", {}).get("apikey", "")
        if key:
            return key
        time.sleep(2)
    warn("Bazarr: could not read its API key after restart -- Homepage's Bazarr widget will need it added by hand")
    return ""


# ---------------------------------------------------------------------------
# Jellyfin: startup wizard API + a permanent API key for Ofelia/Homepage
# ---------------------------------------------------------------------------

def setup_jellyfin():
    log("Configuring Jellyfin")
    r = wait_for_http("http://localhost:8096/System/Info/Public")
    already_done = r.json().get("StartupWizardCompleted", False)

    headers = {"X-Emby-Authorization": JELLYFIN_CLIENT_HEADER}

    if not already_done:
        # GET /Startup/User before POSTing to it -- confirmed against a live
        # instance that skipping the GET makes the POST 404 outright (some
        # lazy-routing quirk on Jellyfin's end), even though nothing in the
        # request itself is wrong.
        requests.get("http://localhost:8096/Startup/User", headers=headers)

        steps = [
            ("Configuration", "http://localhost:8096/Startup/Configuration",
             {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"}),
            ("User", "http://localhost:8096/Startup/User",
             {"Name": ADMIN_USERNAME, "Password": ADMIN_PASSWORD}),
            ("RemoteAccess", "http://localhost:8096/Startup/RemoteAccess",
             {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}),
        ]
        for step_name, url, payload in steps:
            r = requests.post(url, headers=headers, json=payload)
            if not r.ok:
                warn(f"Jellyfin: Startup/{step_name} failed ({r.status_code}) -- aborting setup, run bootstrap again")
                return None
        r = requests.post("http://localhost:8096/Startup/Complete", headers=headers)
        if not r.ok:
            warn(f"Jellyfin: Startup/Complete failed ({r.status_code}) -- aborting setup, run bootstrap again")
            return None
        log("Jellyfin: admin account created")
        wait_for_http("http://localhost:8096/System/Info/Public")

    auth = requests.post(
        "http://localhost:8096/Users/AuthenticateByName",
        headers=headers,
        json={"Username": ADMIN_USERNAME, "Pw": ADMIN_PASSWORD},
    )
    if not auth.ok:
        warn(f"Jellyfin: could not authenticate as {ADMIN_USERNAME} to finish setup ({auth.status_code})")
        return None
    token = auth.json()["AccessToken"]
    auth_headers = {**headers, "X-Emby-Token": token}

    if not already_done:
        folders = requests.get("http://localhost:8096/Library/VirtualFolders", headers=auth_headers).json()
        existing_names = {f["Name"] for f in folders}
        for lib_name, collection_type, path in (
            ("Movies", "movies", "/data/media/movies"),
            ("Shows", "tvshows", "/data/media/tv"),
        ):
            if lib_name in existing_names:
                continue
            r = requests.post(
                "http://localhost:8096/Library/VirtualFolders",
                headers=auth_headers,
                params={"name": lib_name, "collectionType": collection_type, "paths": path, "refreshLibrary": "true"},
            )
            if r.ok:
                log(f"Jellyfin: added library '{lib_name}'")
            else:
                warn(f"Jellyfin: failed to add library '{lib_name}': {r.status_code} {r.text[:200]}")

    keys = requests.get("http://localhost:8096/Auth/Keys", headers=auth_headers).json()
    api_key = next((k["AccessToken"] for k in keys.get("Items", []) if k.get("AppName") == "neoflix-bootstrap"), None)
    if not api_key:
        r = requests.post(
            "http://localhost:8096/Auth/Keys",
            headers=auth_headers,
            params={"app": "neoflix-bootstrap"},
        )
        if r.ok:
            keys = requests.get("http://localhost:8096/Auth/Keys", headers=auth_headers).json()
            api_key = next(k["AccessToken"] for k in keys.get("Items", []) if k.get("AppName") == "neoflix-bootstrap")
            log("Jellyfin: created a permanent API key for Homepage/Ofelia")
        else:
            warn(f"Jellyfin: failed to create API key: {r.status_code} {r.text[:200]}")

    return api_key


def save_jellyfin_api_key(api_key):
    if not api_key:
        return
    env_path = REPO_ROOT / ".env"
    text = env_path.read_text()
    if re.search(r"^JELLYFIN_API_KEY=.*$", text, re.MULTILINE):
        text = re.sub(r"^JELLYFIN_API_KEY=.*$", f"JELLYFIN_API_KEY={api_key}", text, flags=re.MULTILINE)
    else:
        text += f"\nJELLYFIN_API_KEY={api_key}\n"
    env_path.write_text(text)
    log(".env: saved JELLYFIN_API_KEY")


# ---------------------------------------------------------------------------
# Jellyseerr: sign in with the Jellyfin admin account, link Radarr/Sonarr
# ---------------------------------------------------------------------------

def setup_jellyseerr(radarr_key, radarr_profile_id, sonarr_key, sonarr_profile_id):
    log("Configuring Jellyseerr")
    wait_for_http("http://localhost:5055")
    base = "http://localhost:5055/api/v1"
    session = requests.Session()

    r = session.post(
        f"{base}/auth/jellyfin",
        json={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            # hostname must be bare (no scheme) -- port/useSsl/urlBase are
            # separate fields, and serverType=2 (MediaServerType.JELLYFIN) is
            # required or Jellyseerr rejects it as NO_ADMIN_USER even with a
            # valid admin login. None of this is documented in seerr-api.yml;
            # confirmed by reading server/routes/auth.ts directly.
            "hostname": "jellyfin",
            "port": 8096,
            "useSsl": False,
            "urlBase": "",
            "serverType": 2,
        },
    )
    if r.status_code == 500 and "already configured" in r.text:
        # Re-run after Jellyfin's already linked: the hostname fields above
        # are only accepted on first-time setup, a plain login after that.
        r = session.post(f"{base}/auth/jellyfin", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    if not r.ok:
        warn(f"Jellyseerr: sign-in with Jellyfin account failed ({r.status_code}) -- skipping the rest")
        return None
    log("Jellyseerr: signed in with the Jellyfin admin account")

    existing_radarr = session.get(f"{base}/settings/radarr").json()
    if not existing_radarr:
        r = session.post(
            f"{base}/settings/radarr",
            json={
                "name": "Radarr",
                "hostname": "radarr",
                "port": 7878,
                "apiKey": radarr_key,
                "useSsl": False,
                "baseUrl": "",
                "activeProfileId": radarr_profile_id,
                "activeProfileName": "Any",
                "activeDirectory": "/data/media/movies",
                "is4k": False,
                "minimumAvailability": "released",
                "isDefault": True,
            },
        )
        log("Jellyseerr: linked Radarr" if r.ok else f"Jellyseerr: failed to link Radarr ({r.status_code})")

    existing_sonarr = session.get(f"{base}/settings/sonarr").json()
    if not existing_sonarr:
        r = session.post(
            f"{base}/settings/sonarr",
            json={
                "name": "Sonarr",
                "hostname": "sonarr",
                "port": 8989,
                "apiKey": sonarr_key,
                "useSsl": False,
                "baseUrl": "",
                "activeProfileId": sonarr_profile_id,
                "activeProfileName": "Any",
                "activeDirectory": "/data/media/tv",
                "is4k": False,
                "isDefault": True,
                "enableSeasonFolders": True,
            },
        )
        log("Jellyseerr: linked Sonarr" if r.ok else f"Jellyseerr: failed to link Sonarr ({r.status_code})")

    session.post(f"{base}/settings/initialize")
    return session.get(f"{base}/settings/main").json().get("apiKey", "")


# ---------------------------------------------------------------------------
# Uptime Kuma: admin account, monitors, public status page
# ---------------------------------------------------------------------------

MONITORS = [
    ("Jellyfin", "http://jellyfin:8096"),
    ("Radarr", "http://radarr:7878"),
    ("Sonarr", "http://sonarr:8989"),
    ("Bazarr", "http://bazarr:6767"),
    ("Jellyseerr", "http://jellyseerr:5055"),
    ("qBittorrent", "http://qbittorrent:8080"),
    ("Prowlarr", "http://prowlarr:9696"),
    ("Jellyfin Vue", "http://jellyfin-vue:80"),
]


def setup_uptime_kuma():
    log("Configuring Uptime Kuma")
    wait_for_http("http://localhost:3001")
    from uptime_kuma_api import MonitorType, UptimeKumaApi

    api = UptimeKumaApi("http://localhost:3001")
    try:
        if api.need_setup():
            api.setup(ADMIN_USERNAME, ADMIN_PASSWORD)
            log("Uptime Kuma: admin account created")
        api.login(ADMIN_USERNAME, ADMIN_PASSWORD)

        existing = {m["name"]: m["id"] for m in api.get_monitors()}
        monitor_ids = []
        for name, url in MONITORS:
            if name in existing:
                monitor_ids.append(existing[name])
                continue
            result = api.add_monitor(type=MonitorType.HTTP, name=name, url=url, interval=60)
            monitor_ids.append(result["monitorID"])
            log(f"Uptime Kuma: added monitor {name}")

        status_pages = {p["slug"] for p in api.get_status_pages()}
        if "default" not in status_pages:
            api.add_status_page("default", "neoflix status")
            api.save_status_page(
                "default",
                publicGroupList=[{"name": "Services", "weight": 1, "monitorList": [{"id": i} for i in monitor_ids]}],
            )
            log("Uptime Kuma: created public status page (slug: default)")
    finally:
        api.disconnect()


# ---------------------------------------------------------------------------
# Homepage: template a minimal working dashboard from discovered API keys
# ---------------------------------------------------------------------------

def setup_homepage(keys):
    log("Configuring Homepage")
    homepage_dir = DATA_ROOT / "config" / "homepage"
    homepage_dir.mkdir(parents=True, exist_ok=True)
    services_path = homepage_dir / "services.yaml"
    if services_path.exists():
        log("Homepage: services.yaml already exists, leaving it alone")
        return

    services = [
        {"Watch": [
            {"Jellyfin": {"href": "http://localhost:8096", "icon": "jellyfin.png",
                          "widget": {"type": "jellyfin", "url": "http://jellyfin:8096", "key": keys["jellyfin"]}}},
            {"Jellyfin Vue": {"href": "http://localhost:8090", "icon": "jellyfin.png"}},
            {"Jellyseerr": {"href": "http://localhost:5055", "icon": "jellyseerr.png",
                            "widget": {"type": "jellyseerr", "url": "http://jellyseerr:5055", "key": keys["jellyseerr"]}}},
        ]},
        {"Automation": [
            {"Radarr": {"href": "http://localhost:7878", "icon": "radarr.png",
                        "widget": {"type": "radarr", "url": "http://radarr:7878", "key": keys["radarr"]}}},
            {"Sonarr": {"href": "http://localhost:8989", "icon": "sonarr.png",
                        "widget": {"type": "sonarr", "url": "http://sonarr:8989", "key": keys["sonarr"]}}},
            {"Prowlarr": {"href": "http://localhost:9696", "icon": "prowlarr.png",
                          "widget": {"type": "prowlarr", "url": "http://prowlarr:9696", "key": keys["prowlarr"]}}},
            {"Bazarr": {"href": "http://localhost:6767", "icon": "bazarr.png",
                        "widget": {"type": "bazarr", "url": "http://bazarr:6767", "key": keys["bazarr"]}}},
        ]},
        {"Downloads": [
            {"qBittorrent": {"href": "http://localhost:8080", "icon": "qbittorrent.png",
                             "widget": {"type": "qbittorrent", "url": "http://qbittorrent:8080",
                                        "username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}}},
        ]},
        {"Monitoring": [
            {"Uptime Kuma": {"href": "http://localhost:3001", "icon": "uptime-kuma.png",
                             "widget": {"type": "uptimekuma", "url": "http://uptime-kuma:3001", "slug": "default"}}},
        ]},
    ]
    services_path.write_text(yaml.safe_dump(services, sort_keys=False))

    widgets_path = homepage_dir / "widgets.yaml"
    if not widgets_path.exists():
        widgets_path.write_text(yaml.safe_dump([{"datetime": {"format": {"dateStyle": "long"}}}], sort_keys=False))

    settings_path = homepage_dir / "settings.yaml"
    if not settings_path.exists():
        settings_path.write_text(yaml.safe_dump({"title": "neoflix"}, sort_keys=False))

    run(["docker", "restart", "homepage"])
    log("Homepage: wrote a starter dashboard and restarted to apply")
    log(
        "Homepage: this is a minimal starting layout -- weather location, background "
        "rotation, and other cosmetic touches are still yours to add by hand, see SETUP.md"
    )


# ---------------------------------------------------------------------------

def main():
    create_folders()
    docker_compose_up()

    radarr_key = read_arr_api_key("radarr")
    sonarr_key = read_arr_api_key("sonarr")
    prowlarr_key = read_arr_api_key("prowlarr")
    # config.xml existing only means the API key was generated -- Prowlarr's
    # connection test to Radarr/Sonarr needs their web servers actually
    # accepting requests, which can lag behind that by a few seconds.
    wait_for_http("http://localhost:7878")
    wait_for_http("http://localhost:8989")

    bazarr_key = setup_bazarr(radarr_key, sonarr_key)
    setup_qbittorrent()
    setup_prowlarr(prowlarr_key, radarr_key, sonarr_key)
    radarr_profile_id = setup_arr_app("Radarr", 7878, radarr_key, "/data/media/movies")
    sonarr_profile_id = setup_arr_app("Sonarr", 8989, sonarr_key, "/data/media/tv")
    jellyfin_key = setup_jellyfin()
    save_jellyfin_api_key(jellyfin_key)
    jellyseerr_key = setup_jellyseerr(radarr_key, radarr_profile_id, sonarr_key, sonarr_profile_id)

    try:
        setup_uptime_kuma()
    except Exception as e:
        warn(f"Uptime Kuma: {e}")

    setup_homepage({
        "jellyfin": jellyfin_key or "",
        "jellyseerr": jellyseerr_key or "",
        "radarr": radarr_key,
        "sonarr": sonarr_key,
        "prowlarr": prowlarr_key,
        "bazarr": bazarr_key or "",
    })

    print()
    log("Done.")
    if WARNINGS:
        log(f"{len(WARNINGS)} thing(s) still need your attention:")
        for w in WARNINGS:
            print(f"  - {w}")
    else:
        log("Everything configured cleanly.")


if __name__ == "__main__":
    main()

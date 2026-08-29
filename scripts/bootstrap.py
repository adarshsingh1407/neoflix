#!/usr/bin/env python3
"""
One-time post-setup automation for neoflix (see SETUP.md and
DESIGN_DECISIONS.md decision #13). Run directly:

    python3 scripts/bootstrap.py   (macOS/Linux)
    python scripts/bootstrap.py    (Windows)

On first run it creates its own throwaway virtualenv (.bootstrap-venv) and
installs scripts/requirements.txt into it -- nothing gets installed
globally, and nothing but Docker and Python need to already be installed.

Replaces the manual "create an account / paste an API key into every app"
steps with scripted calls to each app's own API, using API keys read
straight off disk. If .env/credentials.env don't exist yet (or
credentials.env still has a placeholder ADMIN_PASSWORD), prompts for the
values interactively and writes them; otherwise reads them straight off
disk, so the same two files also work for non-interactive/scripted setups.

Safe to re-run: every step checks existing state before changing anything.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".bootstrap-venv"
ENV_PATH = REPO_ROOT / ".env"
CREDENTIALS_PATH = REPO_ROOT / "credentials.env"


# ---------------------------------------------------------------------------
# Docker preflight -- fail fast, before spending time on venv/pip work, if
# the one hard external dependency isn't there or isn't running. Checked
# here (not just left to "docker compose up -d" failing later) because a
# missing/unstarted Docker Desktop is the single most likely first-run
# problem, especially on Windows (PATH not refreshed after install, or
# Docker Desktop not started yet).
# ---------------------------------------------------------------------------

def _check_docker():
    if shutil.which("docker") is None:
        sys.exit(
            "Docker not found on PATH. Install Docker Desktop "
            "(https://www.docker.com/products/docker-desktop/), make sure "
            "it's running, and try again."
        )
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(
            "Docker is installed but doesn't seem to be running -- start "
            "Docker Desktop (or the Docker daemon) and try again."
        )


_check_docker()


# ---------------------------------------------------------------------------
# Self-bootstrapping virtualenv -- replaces the old scripts/bootstrap.sh
# wrapper so there's no shell layer at all: this file is the only thing you
# ever run, on every platform. Everything below this needs only stdlib,
# since requests/yaml/tzlocal aren't installed in the outer interpreter yet.
# ---------------------------------------------------------------------------

def _in_venv():
    return sys.prefix != sys.base_prefix


def _venv_python():
    bindir = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return VENV_DIR / bindir / exe


def _ensure_venv_and_reexec():
    if _in_venv():
        return
    if not VENV_DIR.exists():
        print("[bootstrap] Creating a virtualenv for setup dependencies (one-time)...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        except subprocess.CalledProcessError:
            sys.exit(
                "Failed to create a virtualenv. On Debian/Ubuntu you may need "
                "the venv module installed separately, e.g.:\n"
                "  sudo apt install python3-venv"
            )
    vpy = str(_venv_python())
    subprocess.run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run(
        [vpy, "-m", "pip", "install", "--quiet", "-r", str(REPO_ROOT / "scripts" / "requirements.txt")],
        check=True,
    )
    sys.stdout.flush()
    os.execv(vpy, [vpy, str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv_and_reexec()

# Everything past this point can assume it's running inside .bootstrap-venv
# with scripts/requirements.txt already installed.

import getpass
import json
import re
import time

import requests
import yaml

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


def _set_env_key(path, key, value, only_if_absent=False):
    """Create or update `path`, setting KEY=value. If only_if_absent, an
    already-present value is left untouched -- used for user preferences
    (PUID/PGID/TZ) that shouldn't get silently reverted on a re-run, as
    opposed to derived values (NEOFLIX_REPO_ROOT, JELLYFIN_API_KEY) that
    should always reflect current reality."""
    text = path.read_text() if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    existing = pattern.search(text)
    if existing and only_if_absent:
        return
    line = f"{key}={value}"
    if existing:
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    with path.open("w", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Interactive first-run wizard -- only runs when .env/credentials.env are
# missing, or credentials.env still has the placeholder ADMIN_PASSWORD.
# Additive, not a replacement: the *.env.example files and hand-editing
# still work exactly as before for scripted/non-interactive setups.
# ---------------------------------------------------------------------------

def _prompt(question, default=None):
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{question}{suffix}: ").strip()
        if answer:
            return answer
        if default is not None:
            return default


def _prompt_yes_no(question, default=False):
    suffix = " [Y/n]" if default else " [y/N]"
    answer = input(f"{question}{suffix}: ").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def _prompt_password(question):
    while True:
        pw1 = getpass.getpass(f"{question}: ")
        if not pw1:
            continue
        pw2 = getpass.getpass("  confirm: ")
        if pw1 == pw2:
            return pw1
        print("  those didn't match -- try again")


def _prompt_data_root():
    default = str(Path.home() / "neoflix-data")
    while True:
        answer = _prompt("Where should movies/shows/app config live (DATA_ROOT)?", default)
        candidate = Path(answer).expanduser().resolve()
        if candidate == REPO_ROOT or REPO_ROOT in candidate.parents:
            print(
                f"  {candidate} is inside this repo folder -- pick a location "
                "outside it (it must never get swept up into git; see SETUP.md)."
            )
            continue
        return candidate.as_posix()


_DATA_ROOT_PLACEHOLDER = "/Users/YOUR_USERNAME/neoflix-data"


def _needs_env_wizard():
    # Also re-triggers if .env exists but DATA_ROOT is still .env.example's
    # placeholder -- symmetric with credentials.env's ADMIN_PASSWORD check
    # below, for someone who copied the template and didn't finish editing.
    env = load_env_file(ENV_PATH)
    return not ENV_PATH.exists() or env.get("DATA_ROOT", "") in ("", _DATA_ROOT_PLACEHOLDER)


def _needs_credentials_wizard():
    creds = load_env_file(CREDENTIALS_PATH)
    return creds.get("ADMIN_PASSWORD", "") in ("", "changeme")


def _run_config_wizard(need_env, need_credentials):
    print("[bootstrap] First-time setup -- a few questions to create the config files.")
    print("[bootstrap] (Ctrl-C cancels at any point; nothing is written until you're done.)")
    print()

    data_root = None
    username = password = None
    os_username = os_password = ""

    try:
        if need_env:
            data_root = _prompt_data_root()

        if need_credentials:
            existing = load_env_file(CREDENTIALS_PATH)
            username = _prompt(
                "Admin username (reused for qBittorrent/Jellyfin/Uptime Kuma)",
                existing.get("ADMIN_USERNAME") or "admin",
            )
            password = _prompt_password("Admin password")

            os_username = existing.get("OPENSUBTITLES_USERNAME", "")
            os_password = existing.get("OPENSUBTITLES_PASSWORD", "")
            if not (os_username and os_password):
                if _prompt_yes_no("Set up automatic subtitles via OpenSubtitles.com?", default=False):
                    os_username = _prompt("OpenSubtitles.com username")
                    os_password = _prompt_password("OpenSubtitles.com password")
    except (KeyboardInterrupt, EOFError):
        sys.exit("\n[bootstrap] Setup cancelled -- no files were written.")

    if need_env:
        _set_env_key(ENV_PATH, "DATA_ROOT", data_root)
        log(".env: saved")

    if need_credentials:
        _set_env_key(CREDENTIALS_PATH, "ADMIN_USERNAME", username)
        _set_env_key(CREDENTIALS_PATH, "ADMIN_PASSWORD", password)
        _set_env_key(CREDENTIALS_PATH, "OPENSUBTITLES_USERNAME", os_username)
        _set_env_key(CREDENTIALS_PATH, "OPENSUBTITLES_PASSWORD", os_password)
        log("credentials.env: saved")

    print()


def _ensure_config_files():
    need_env = _needs_env_wizard()
    need_credentials = _needs_credentials_wizard()
    if not need_env and not need_credentials:
        return

    if not sys.stdin.isatty():
        missing = []
        if need_env:
            missing.append(".env (copy .env.example and fill it in)")
        if need_credentials:
            missing.append("credentials.env with a real ADMIN_PASSWORD (copy credentials.env.example and fill it in)")
        sys.exit("Missing setup: " + "; ".join(missing) + ".")

    _run_config_wizard(need_env, need_credentials)


_ensure_config_files()

ENV = {**load_env_file(ENV_PATH), **load_env_file(CREDENTIALS_PATH)}

# A Windows-pasted backslash DATA_ROOT would otherwise land inside the
# Ofelia volume label's JSON-array string and break parsing (backslash is a
# JSON escape character) -- normalize to forward slashes, Docker Desktop
# accepts that form natively on every platform.
if "\\" in ENV["DATA_ROOT"]:
    _normalized = Path(ENV["DATA_ROOT"]).expanduser().as_posix()
    _set_env_key(ENV_PATH, "DATA_ROOT", _normalized)
    ENV["DATA_ROOT"] = _normalized
    log(".env: normalized DATA_ROOT to forward-slash form")

DATA_ROOT = Path(ENV["DATA_ROOT"]).expanduser()
ADMIN_USERNAME = ENV.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = ENV.get("ADMIN_PASSWORD", "")
OPENSUBTITLES_USERNAME = ENV.get("OPENSUBTITLES_USERNAME", "")
OPENSUBTITLES_PASSWORD = ENV.get("OPENSUBTITLES_PASSWORD", "")

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
# Step 1: PUID/PGID/TZ auto-detection, folders (replaces SETUP.md's old
# manual `id -u`/`readlink` steps and folder-creation step)
# ---------------------------------------------------------------------------

def ensure_puid_pgid_tz():
    if hasattr(os, "getuid"):
        puid, pgid = str(os.getuid()), str(os.getgid())
    else:
        # No uid/gid concept on Windows; this is linuxserver.io's own
        # documented default, and Docker Desktop's Windows backend
        # translates bind-mount permissions itself, so it isn't
        # load-bearing there the way it is on macOS/Linux.
        puid, pgid = "1000", "1000"
    _set_env_key(ENV_PATH, "PUID", puid, only_if_absent=True)
    _set_env_key(ENV_PATH, "PGID", pgid, only_if_absent=True)

    try:
        from tzlocal import get_localzone_name
        tz = get_localzone_name() or "Etc/UTC"
    except Exception as e:
        warn(f"Could not auto-detect timezone ({e}) -- defaulting to Etc/UTC, edit TZ in .env by hand if that's wrong")
        tz = "Etc/UTC"
    _set_env_key(ENV_PATH, "TZ", tz, only_if_absent=True)


def create_folders():
    log(f"Creating data folders under {DATA_ROOT}")
    try:
        for d in ("movies", "tv", "music", "books", "audiobooks", "comics"):
            (DATA_ROOT / "downloads" / d).mkdir(parents=True, exist_ok=True)
        for d in ("movies", "tv", "anime", "music", "books", "audiobooks", "comics"):
            (DATA_ROOT / "media" / d).mkdir(parents=True, exist_ok=True)
        for app in (
            "jellyfin", "sonarr", "radarr", "prowlarr", "qbittorrent",
            "jellyseerr", "bazarr", "homepage", "uptime-kuma",
        ):
            (DATA_ROOT / "config" / app).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(
            f"Couldn't create data folders under {DATA_ROOT}: {e}\n"
            "Check that DATA_ROOT in .env is a valid, writable path."
        )


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
    base = "http://localhost:9696/api/v1"
    headers = {"X-Api-Key": prowlarr_key}
    # The root page can respond (wait_for_http's status_code < 500 check)
    # before Prowlarr's API/DB layer is actually warmed up -- wait on an
    # authenticated API endpoint instead, same fix as the Radarr/Sonarr
    # readiness race already handled in main().
    wait_for_http(f"{base}/system/status", headers=headers)

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
                # Without this, Jellyfin defaults new libraries to no internet metadata lookups
                # and no filesystem watching -- new imports sit with bare filenames until a
                # scheduled scan (or manual refresh) happens to pull TMDB data.
                json={"LibraryOptions": {"EnableInternetProviders": True, "EnableRealtimeMonitor": True}},
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
    _set_env_key(ENV_PATH, "JELLYFIN_API_KEY", api_key)
    log(".env: saved JELLYFIN_API_KEY")

    # Ofelia's poster-grid/rotate-background jobs get JELLYFIN_API_KEY baked
    # into their labels at container-creation time, not read live from .env
    # -- ofelia was already started (docker_compose_up ran before this key
    # existed) with that value blank, so it needs recreating now that it's
    # real, or both scheduled jobs would silently run with no API key.
    r = run(["docker", "compose", "up", "-d", "ofelia"], cwd=REPO_ROOT)
    if r.returncode != 0:
        warn("ofelia: failed to recreate with the new JELLYFIN_API_KEY -- poster-grid/rotate-background jobs will fail until you run 'docker compose up -d ofelia' yourself")
    else:
        log("ofelia: recreated so poster-grid/rotate-background jobs pick up JELLYFIN_API_KEY")


def save_lifecycle_api_keys(radarr_key, sonarr_key, jellyseerr_key):
    if not (radarr_key and sonarr_key and jellyseerr_key):
        warn("lifecycle: missing one or more API keys -- skipping, request-lifecycle widget won't work until you set RADARR_API_KEY/SONARR_API_KEY/JELLYSEERR_API_KEY in .env and run 'docker compose up -d lifecycle' yourself")
        return
    _set_env_key(ENV_PATH, "RADARR_API_KEY", radarr_key)
    _set_env_key(ENV_PATH, "SONARR_API_KEY", sonarr_key)
    _set_env_key(ENV_PATH, "JELLYSEERR_API_KEY", jellyseerr_key)
    log(".env: saved RADARR_API_KEY, SONARR_API_KEY, JELLYSEERR_API_KEY")

    # Same situation as JELLYFIN_API_KEY above: lifecycle's environment block
    # is baked in at container-creation time, and docker_compose_up already
    # started it (with these keys blank) before any of them existed.
    r = run(["docker", "compose", "up", "-d", "lifecycle"], cwd=REPO_ROOT)
    if r.returncode != 0:
        warn("lifecycle: failed to recreate with the new API keys -- run 'docker compose up -d lifecycle' yourself")
    else:
        log("lifecycle: recreated so it picks up its API keys")


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
    ("Homepage", "http://homepage:3000"),
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
            {"Jellyseerr": {"href": "http://localhost:5055", "icon": "jellyseerr.png",
                            "widgets": [
                                {"type": "seerr", "url": "http://jellyseerr:5055", "key": keys["jellyseerr"]},
                                {"type": "customapi", "url": "http://lifecycle:8100/status",
                                 "display": "dynamic-list",
                                 "mappings": {"items": "items", "name": "name", "label": "label"}},
                            ]}},
        ]},
        {"Automation": [
            {"Radarr": {"href": "http://localhost:7878", "icon": "radarr.png",
                        "widget": {"type": "radarr", "url": "http://radarr:7878", "key": keys["radarr"],
                                   "fields": ["missing", "movies"]}}},
            {"Sonarr": {"href": "http://localhost:8989", "icon": "sonarr.png",
                        "widget": {"type": "sonarr", "url": "http://sonarr:8989", "key": keys["sonarr"],
                                   "fields": ["wanted", "series"]}}},
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
    ensure_puid_pgid_tz()
    _set_env_key(ENV_PATH, "NEOFLIX_REPO_ROOT", REPO_ROOT.as_posix())
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
    save_lifecycle_api_keys(radarr_key, sonarr_key, jellyseerr_key)

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

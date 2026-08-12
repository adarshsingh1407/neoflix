# neoflix — Setup From Scratch

This guide assumes you already know **git**, know what **Docker** is and
have it installed, and are comfortable running commands in a terminal. It
does **not** assume you know anything about the apps this project uses —
every term gets explained the first time it comes up. Just follow the steps
in order.

## What you're actually building

This sets up a core stack of small programs (each running in its own
Docker container) that work together, plus a few optional extras. Worth
knowing what each one does before you start, since the setup steps will
make more sense:

| App | What it actually does |
|---|---|
| **Jellyfin** | The screen you actually watch things on — like a personal Netflix. This is the only app regular household members need to open. |
| **Jellyseerr** | The "search and request" screen. You search for a movie/show here and click Request — everything after that happens automatically. |
| **Radarr** | Works behind the scenes for movies — once you request one, it finds it and hands it to the download app. |
| **Sonarr** | Same as Radarr, but for TV shows. |
| **Prowlarr** | The search engine Radarr/Sonarr use to actually find downloadable copies of things. |
| **qBittorrent** | Does the actual downloading. |
| **Bazarr** *(optional)* | Automatically finds and downloads subtitles for anything you get. |
| **Homepage** *(optional)* | One dashboard page with links to everything above, plus live status widgets. The setup script gives you a working starting version. |
| **Uptime Kuma** *(optional)* | Watches all the other apps and tells you if one goes down. |
| **Jellyfin Vue** *(optional)* | An alternative, more modern-looking way to browse/watch — same server as Jellyfin, just a different screen. Try it, remove it if you don't like it. |

There's also **Ofelia**, a scheduler running quietly in the background —
it has no web page of its own, nothing to set up, nothing to open.

You'll only ever use **Jellyfin** and **Jellyseerr** day-to-day. The rest
you set up once and then mostly leave alone.

## 1. Prerequisites

- Docker Desktop installed and running (you should see its icon/whale in
  your system tray or menu bar — if it's not running, start it now)
- Python 3 (macOS ships with this already — check with `python3 --version`).
  Only needed for the setup script in step 5; it builds a throwaway
  virtualenv for its own dependencies, nothing gets installed globally.

## 2. Get the files

```sh
git clone <this repo's URL>
cd neoflix
```

(Or however you'd normally get a repo onto your machine.)

## 3. Fill in your settings

This project reads its settings from a file called `.env`. Create your own
copy from the template:

```sh
cp .env.example .env
```

Now open `.env` in any text editor and fill in four things:

- **`PUID`** and **`PGID`** — two numbers that identify your user account to
  Docker, so the files these apps create belong to you instead of some
  internal system account. Get them by running:
  ```sh
  id -u   # this number goes in PUID
  id -g   # this number goes in PGID
  ```
- **`TZ`** — your timezone, so schedules and air-dates line up correctly.
  Example: `America/New_York`. On a Mac, you can find yours with:
  ```sh
  readlink /etc/localtime | sed 's#.*/zoneinfo/##'
  ```
- **`DATA_ROOT`** — a folder path **outside this repo folder** where all
  your actual movies, shows, and app settings will be stored. For example
  `/Users/yourname/neoflix-data`. It doesn't need to exist yet — you'll
  create it in the next step. Keeping it outside the repo just means it
  never accidentally gets swept up into git.

## 4. Add your credentials

This is the only manual account setup left — everything else in step 5 is
scripted. Create your own copy of the credentials template:

```sh
cp credentials.env.example credentials.env
```

Open `credentials.env` and fill in:

- **`ADMIN_USERNAME`** / **`ADMIN_PASSWORD`** — one login, reused for every
  app that needs a new account created (qBittorrent, Jellyfin, Uptime
  Kuma). Pick a real password; these are reachable on your LAN.
- **`OPENSUBTITLES_USERNAME`** / **`OPENSUBTITLES_PASSWORD`** *(optional)* —
  Bazarr's subtitle provider needs a real
  [OpenSubtitles.com](https://www.opensubtitles.com/en/users/newuser)
  account. Leave blank to skip subtitles for now; add it later by hand in
  Bazarr's UI if you change your mind.

`credentials.env` is gitignored, same as `.env` — it never gets committed.

## 5. Run the setup script

```sh
scripts/bootstrap.sh
```

This one command replaces the folder-creation and one-time-per-app setup
that used to be manual: it creates the data folders, starts the stack,
waits for each app to generate its own API key, and then wires everything
together — download client, indexers, root folders, a permissive quality
profile, Jellyfin's admin account and libraries, Jellyseerr, Bazarr's
Radarr/Sonarr connections, Uptime Kuma's monitors and status page, and a
starter Homepage dashboard. It needs Python 3 (to build a throwaway
virtualenv for its dependencies) alongside Docker. Safe to re-run if it
fails partway — every step checks existing state first.

It prints a summary at the end, including anything it couldn't do for you.
Two things are *never* automated, by design:

- **Bazarr's subtitle language profile** — stored in Bazarr's own database,
  not a file or documented API. One-time pick in **Settings → Languages**.
- **The OpenSubtitles account itself** — has to be a real account you
  control; the script only wires in credentials you already have.

Check everything started:

```sh
docker compose ps
```

You should see a row per app, all saying "Up" under status.

**Prefer to do it by hand, or want to know what the script is actually
doing?** See the [manual setup appendix](#manual-setup-appendix) at the
bottom of this file — same steps, one app at a time.

## 6. Try it out

1. Open Jellyseerr, search for any movie, click **Request**
2. Wait a minute or two, then check qBittorrent — you should see it start
   downloading
3. Once it finishes, open Jellyfin — it should just appear in your library,
   no extra steps needed

If nothing happens after a few minutes and you ran `bootstrap.sh`, check its
summary output for warnings first — it prints exactly what it couldn't do
for you. If you set things up by hand, the two most common causes are:
missing the Prowlarr → Apps link, or the quality profile being too strict
(see the **Prowlarr** and **Radarr and Sonarr** sections in the manual
setup appendix below).

## If something's not working

- **A page won't load right after startup** — give it another 30 seconds
  and refresh. Some apps are slower to start than others.
- **A request just sits there doing nothing** — check that Prowlarr knows
  about Radarr/Sonarr, and that the quality profile isn't too strict (see
  the manual appendix). These two cause the vast majority of "nothing's
  happening."
- **Random one-off connection errors** to outside websites — this happens
  occasionally and almost always fixes itself if you just try the same
  thing again a minute later. Not something to worry about unless it keeps
  failing repeatedly.
- **Something that worked yesterday stops connecting** — if your home
  network reassigns your computer a new address sometimes, apps that
  bookmarked the old address will stop working. Re-check your computer's
  current local address and update your bookmarks if needed.
- **A specific movie/show just won't find anything, even after checking
  the above** — use **Interactive Search** to see for yourself instead of
  trusting the automatic pick. In Radarr or Sonarr, open the movie/show,
  and look for a **magnifying glass icon** near the top of the page (it's
  labeled "Interactive Search," separate from the plain "Search" button).
  This shows you every option it found, including ones it rejected and
  why — you can then pick one yourself by clicking its download icon.
  This is especially useful for older, foreign, or less common titles that
  the automatic search sometimes struggles to match correctly.

## Everything you can now open

Once setup is done, here's every address you now have running, and what
each one is actually for:

| Address | App | What it's for |
|---|---|---|
| `http://localhost:8096` | **Jellyfin** | Where you actually watch things. This is the one to bookmark on your phone/TV. |
| `http://localhost:5055` | **Jellyseerr** | Where you search for and request new movies/shows. |
| `http://localhost:7878` | **Radarr** | Behind-the-scenes movie manager. You won't need this day-to-day, but it's where you'd check on a movie's status or fix a setting. |
| `http://localhost:8989` | **Sonarr** | Same as Radarr, but for TV shows. |
| `http://localhost:9696` | **Prowlarr** | Manages which sites Radarr/Sonarr are allowed to search. Set-and-forget once linked up. |
| `http://localhost:8080` | **qBittorrent** | Shows active/finished downloads in progress. Worth a peek if something seems stuck. |
| `http://localhost:6767` | **Bazarr** | Subtitle manager, if you're using it. |
| `http://localhost:3001` | **Uptime Kuma** | Shows whether everything's actually up, if you're using it. |
| `http://localhost:3000` | **Homepage** | One dashboard with links to everything above, if you're using it. |
| `http://localhost:8090` | **Jellyfin Vue** | Alternative way to browse/watch — same server as Jellyfin, different screen. |

`localhost` only works on the same computer running Docker. To reach these
from your phone or another device on the same WiFi, swap `localhost` for
that computer's local network address (on a Mac: `ipconfig getifaddr en0`).

## (Optional) Stop your computer's address from changing

Home networks often reassign your computer a slightly different local
address every so often (this is called DHCP). Most of the time you won't
notice, but it'll break any bookmark you've saved on your phone or TV
pointing at the old address. Fixing this once is worth it if that's
happened to you.

The reliable way to fix this is a **DHCP reservation** — you tell your
router "always give this specific computer the same address," which is a
one-time setting on the router itself, not on the Mac.

1. **Find your Mac's hardware address (MAC address)** — this uniquely
   identifies your Mac's network hardware, separate from its current IP
   address. Run:
   ```sh
   ifconfig en0 | grep ether
   ```
   You'll get something like `ether a1:b2:c3:d4:e5:f6` — that's it.

2. **Log into your router's admin page.** Usually this is done by typing
   an address like `192.168.1.1` or `192.168.0.1` into a browser (not
   Docker-related — this is your actual physical router/WiFi box). If you
   don't know the login, check for a sticker on the router itself, or ask
   whoever originally set up your home WiFi.

3. **Find the DHCP reservation setting.** The exact name and location
   varies a lot by router brand — look for something called **"DHCP
   Reservation," "Address Reservation," "Static DHCP,"** or **"IP-MAC
   Binding."** It's usually under a "Network," "LAN," or "DHCP" settings
   section.

4. **Add a new reservation**, pairing the MAC address from step 1 with an
   IP address of your choice. The simplest option is to just reserve
   whatever address your Mac currently has (check with
   `ipconfig getifaddr en0`) — that way none of your existing bookmarks
   need to change.

5. **Save**, and restart your Mac's WiFi/network connection (or just
   reboot) to confirm it picks up the reserved address.

If you can't access your router's settings at all (e.g. it's managed by
someone else, or your ISP locks it down), a fallback is setting a static
IP directly on the Mac instead: **System Settings → Network → Wi-Fi (or
Ethernet) → Details → TCP/IP → Configure IPv4: Manually.** This is less
reliable though — if your router later happens to hand that same address
to a different device, you'll get a conflict. The router-side reservation
above is the better fix if you have any access to it at all.

## Manual setup appendix

Everything below is what `scripts/bootstrap.sh` does for you automatically
(decision #13). Use this if you'd rather click through it yourself, need
to fix up one specific app after the script partially failed, or are just
curious what it's actually doing under the hood. Each app has its own web
page you'll open in a browser — if a page doesn't load right away, wait
20-30 seconds and refresh, some apps take a moment to finish starting.

### qBittorrent — the download app

Open **`http://localhost:8080`**

1. qBittorrent needs a password the very first time. Find it by running:
   ```sh
   docker logs qbittorrent | grep -i "temporary password"
   ```
   Log in with username `admin` and that password.
2. That temporary password resets every time the app restarts, so set a
   real one now: click the **hamburger menu (☰) → Options → Downloads**
   (exact menu names vary slightly by version, look for "Web UI" settings)
   and set a permanent username/password you'll remember.
3. **Important:** while you're in settings, find "Default Save Path" and
   set it to `/data/downloads`. Without this, downloads will fail silently.

### Prowlarr — the search engine

Open **`http://localhost:9696`**

1. Click **Indexers → Add Indexer**. An "indexer" here just means a
   website Prowlarr is allowed to search. Add a couple of well-known free
   ones — search for "1337x", "YTS", or "The Pirate Bay" in the add-indexer
   list and enable them. No account/signup needed for these.
2. Now the important part — click **Settings → Apps → Add Application**,
   and add both:
   - **Radarr**: address `http://radarr:7878`
   - **Sonarr**: address `http://sonarr:8989`

   For each one, you'll need that app's **API key** — think of this as a
   password the apps use to talk to each other automatically, so you don't
   have to. Find it by opening Radarr (`http://localhost:7878`) or Sonarr
   (`http://localhost:8989`) in another tab, going to **Settings →
   General**, and copying the API Key shown there. Paste it into Prowlarr.

   **Don't skip this step** — without it, Prowlarr's searches never
   actually reach Radarr/Sonarr, and things will just silently not work
   with no obvious error telling you why.

### Radarr and Sonarr — the movie/show organizers

Open Radarr (**`http://localhost:7878`**) and repeat these for Sonarr
(**`http://localhost:8989`**) too — the steps are identical, just for shows
instead of movies.

1. **Settings → Download Clients → Add → qBittorrent.** Host `qbittorrent`,
   port `8080`, and the username/password you set in qBittorrent's step
   above.
2. **Settings → Media Management → Root Folders → Add.** This is the
   folder where finished movies/shows get placed. Use `/data/media/movies`
   for Radarr, `/data/media/tv` for Sonarr.
3. **Settings → Profiles.** These control which video quality you're
   willing to accept. The default profile is stricter than you'd expect —
   it can reject perfectly good downloads just because of how they were
   encoded. To avoid confusing "nothing's downloading" moments later, pick
   (or create) a profile called **"Any"** and use that for now — you can
   always tighten it up later once things are working.

### Jellyseerr — the request screen

Open **`http://localhost:5055`** and follow its setup wizard:

1. Choose **Jellyfin** as your media server
2. Sign in (you'll create your actual Jellyfin login in the next step, so
   if this is your very first time, do the Jellyfin step first, then come
   back here)
3. Add Radarr and Sonarr as backing servers, same connection details as
   above — and again, pick the **"Any"** quality profile here too, for the
   same reason as before.

### Jellyfin — the screen you actually watch on

Open **`http://localhost:8096`** and follow its setup wizard: create your
admin account, then add two libraries:

- **Movies**, pointing at `/data/media/movies`
- **Shows**, pointing at `/data/media/tv`

### Bazarr — automatic subtitles (optional, skip if you don't need this)

Open **`http://localhost:6767`**

1. **Settings → Radarr** and **Settings → Sonarr** — same connection
   details as everywhere else (hostname, port, API key)
2. **Settings → Languages → Add New Profile** — add the language(s) you
   want subtitles in, save
3. Still on that page, set this profile as the **default** for both
   Movies and Series (separate dropdowns further down)
4. **Settings → Providers → Add → OpenSubtitles.com** — enter your own
   free account details (make one at opensubtitles.com first if you don't
   have one)

### Uptime Kuma — status monitoring (optional, skip if you don't need this)

Open **`http://localhost:3001`**

1. It'll show a **"Create your admin account"** screen the first time —
   pick a username and password (this is a real account for a service
   reachable on your network, so use a real password here, not something
   throwaway).
2. Click **Add New Monitor** for each app you want watched. For each one,
   set **Monitor Type** to `HTTP(s)`, give it a name, and use the
   container-name URL — e.g. Jellyfin is `http://jellyfin:8096`, Radarr is
   `http://radarr:7878`, and so on (same hostnames used throughout this
   guide).
3. Once you've added your monitors, go to **Status Pages → New Status
   Page**, set the **Slug** field to exactly `default`, add all your
   monitors to it, and **Save**. The slug has to be `default` — that's
   what `config/homepage/services.yaml` is already set up to read from, if
   you're also using Homepage (below).

### Homepage — the links dashboard (optional, skip if you don't need this)

Unlike every other app above, Homepage has **no setup wizard** — it's
configured entirely by hand-editing YAML files under
`$DATA_ROOT/config/homepage/` (`services.yaml` for your links/widgets,
`widgets.yaml` for the clock/weather, `settings.yaml` for layout/theme).
`bootstrap.sh` writes a minimal starting version of these; weather
location, background rotation, and other cosmetic touches are still yours
to add by hand. See [gethomepage.dev](https://gethomepage.dev) for the
config schema. After editing, `docker restart homepage` to pick up
changes — Homepage doesn't hot-reload its config.

## Curious why any of this is built this way?

This guide is just the "how" — see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)
for the "why" behind every choice, or [USER_STORIES.md](USER_STORIES.md)
for a more thorough test checklist once you're up and running.

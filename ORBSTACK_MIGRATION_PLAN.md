# OrbStack migration plan (executed 2026-11-09; outcome in DESIGN_DECISIONS.md #18)

Researched 2026-11-09 by a read-only planning pass; nothing was installed or
changed. Facts cite their source; anything marked *unverified* is tested by a
step below.

## Summary

- **Why:** Docker Desktop keeps its whole VM allocation resident (measured with
  `footprint`: 2610 MB for a 2.5 GB VM). OrbStack allocates on demand and
  returns unused memory to macOS
  ([docs](https://docs.orbstack.dev/efficiency),
  [blog](https://orbstack.dev/blog/dynamic-memory)). The core six use ~1.0 GB
  idle, so the expected saving is roughly 1–1.5 GB (*unverified*: measure it).
- **Method:** a clean re-pull of the six core images **by digest** (so nothing
  upgrades mid-migration and rollback stays safe), not `orb docker migrate`.
  There are no named volumes; all state is in `${DATA_ROOT}` bind mounts.
- **Settings:** OrbStack ≥ 2.2.5 (macOS 27 fixes, UDP port-forward fix),
  `memory_mib 3072`, `cpu 4`, expose ports to LAN **on** (default),
  Kubernetes never started, Rosetta off.

## Measured on this machine

- All 13 local images are `linux/arm64`, so Rosetta is never needed.
- `docker volume ls` is empty. Build cache (228 MB) is disposable.
- Use `footprint -p <pid>` or Activity Monitor to measure the VM. `ps` RSS
  showed 275 MB for a 2.6 GB VM.
- The docker CLI, Compose and buildx are all symlinks into `Docker.app`.
  `~/.docker/config.json` uses `"credsStore": "desktop"`.
- `Docker.raw` uses 9.5 GB actually on disk (245 GB apparent).
- `neoflix-net` has IPv6 on, with an automatically assigned ULA subnet.

### Core image versions to pin

| Image | Version |
|---|---|
| jellyfin | 12.4ubu2604-ls58 |
| radarr | 6.4.4.10685-ls325 |
| sonarr | 4.0.20.3014-ls2 |
| prowlarr | 2.7.0.5723-ls167 |
| qbittorrent | 5.3.0_v2.1.2-ls486 |
| bazarr | v1.6.2-ls371 |

## OrbStack facts that matter here

| Topic | Fact | Source |
|---|---|---|
| Memory | Dynamic; unused memory is returned to macOS. `memory_mib` is a cap. | [settings](https://docs.orbstack.dev/settings) |
| CPU | `cpu` is a cap, not a reservation. | same |
| Ports | Reachable from the LAN **by default** (`docker.expose_ports_to_lan`). | [network](https://docs.orbstack.dev/docker/network) |
| IPv6 | **Off for containers by default.** `neoflix-net` may fail to create until it's enabled. | same |
| Bind mounts | Tuned VirtioFS. Hardlink behaviour is **not documented**. | [file-sharing](https://docs.orbstack.dev/docker/file-sharing) |
| docker.sock | Takes over `/var/run/docker.sock` (with an admin prompt). Context is `orbstack`. | [install](https://docs.orbstack.dev/install) |
| GPU | Not supported (same as Docker Desktop). | [faq](https://docs.orbstack.dev/faq) |
| Licence | Free for personal, non-commercial use. | [pricing](https://orbstack.dev/pricing) |

## Steps

1. **Back up config** (after the containers are stopped in step 2):
   ```sh
   tar -C /Users/adarshsingh/neoflix-data -czf ~/neoflix-config-pre-orb-$(date +%F).tgz --exclude='config/jellyfin/cache' config
   ```
2. **Stop the stack on Docker Desktop**, keeping its containers for rollback:
   ```sh
   docker context use desktop-linux && docker compose stop
   ```
   Then quit Docker Desktop, and check that `pgrep -fl Virtualization.VirtualMachine` prints nothing.
   **Never run both engines with the stack at once:** they share SQLite config and ports.
3. **Install OrbStack ≥ 2.2.5:** `brew install --cask orbstack`.
   - Decline its offer to migrate Docker Desktop data.
   - Allow the admin prompt that takes over the docker socket.
4. **Set limits before starting anything:**
   ```sh
   orb config set memory_mib 3072
   orb config set cpu 4
   orb config set rosetta false
   orb config show
   ```
   `orb config show` should also list `expose_ports_to_lan: true`.
5. **Switch to OrbStack:** `docker context use orbstack`, then check `docker info`.
6. **Pull the core six by digest** and tag each back to `:latest`, so the compose file stays unchanged. Get the full digests with:
   ```sh
   docker image inspect --format '{{index .RepoDigests 0}}' lscr.io/linuxserver/<name>:latest
   ```
   Run it on Docker Desktop **before** step 2.
7. **Start the core services:** `docker compose up -d`. If `neoflix-net` fails on IPv6:
   - turn on OrbStack Settings → Docker → Enable IPv6, or
   - if that doesn't work, decide whether to drop `enable_ipv6` from compose.
8. **Check the memory caps:** `docker stats --no-stream` should show the per-service limits, not the VM total.
9. **Extras only when needed.** Before relying on Ofelia, test the docker socket:
   ```sh
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock docker:cli docker ps
   ```

## Verify

- **Ports:** every core port answers from `127.0.0.1` **and** the LAN IP. `lsof` should show `*:8096`, not `127.0.0.1`.
- **TV playback:** play a title on the Tizen and Android TV apps, including one seek.
- **Torrents:** qBittorrent shows as connected with peers, and the VueTorrent mod installed.
- **Hardlinks:** in a scratch folder, `ln` inside the radarr container should give the same inode and link count 2 when checked on the Mac. Check again after the next real import.
- **Dashboard:** all dots on `:3000` are green.
- **No OOM kills:** `docker inspect <name> --format '{{.State.OOMKilled}}'` is false after a day.
- **Memory:** `footprint` for the VM, plus `orb top`, `sysctl vm.swapusage`. Measure idle and after a transcode, and compare with 2610 MB.

## Rollback

1. Stop the stack on OrbStack and quit OrbStack:
   ```sh
   docker context use orbstack && docker compose stop
   ```
   Then `orb stop`, and turn off OrbStack's start at login.
2. Restore the config tarball only if the old images won't start. Pinning by digest should make this unnecessary.
3. Start Docker Desktop and run `docker context use desktop-linux`. If `/var/run/docker.sock` still points to OrbStack, fix the symlink.
4. Restart the stack with `docker compose start`, then re-run the Verify checks.

## After it's proven (2–4 weeks)

- **Reclaim disk:** purge Docker Desktop data, which frees about 9.5 GB.
- **Before uninstalling Docker Desktop, fix the CLI setup:**
  - In `~/.docker/config.json`, set `credsStore` to `osxkeychain` and remove the Docker Desktop plugin/hook entries.
  - Remove the dangling `~/.docker/cli-plugins` and `/usr/local/bin/docker*` symlinks.
  - Restart OrbStack so it installs its own CLI, Compose and buildx.
- **Update the docs:**
  - DESIGN_DECISIONS #16: the VM sizing becomes the OrbStack settings, with before/after numbers.
  - DESIGN_DECISIONS #5, #8, #9, #11 and #17: reword the Docker Desktop mentions.
  - Add a new decision #18 recording the migration.
  - CLAUDE.md house rule 6, SETUP.md prerequisites, HLD.md diagram title, GOALS.md and the compose comments.
  - `bootstrap.py` error text.

## Risks, most serious first

1. **Rollback broken by a schema upgrade** if `:latest` gets re-pulled. Mitigation: pin by digest and keep the tarball.
2. **Hardlinks on OrbStack VirtioFS aren't documented.** If they fail, imports silently copy and double disk use, with 49 GB free. Mitigation: the scratch test.
3. **The IPv6 network may fail to create**, since OrbStack has IPv6 off by default.
4. **Removing Docker.app** breaks the CLI and credentials unless `~/.docker` is cleaned up first.
5. **Ofelia's socket mount** and **start-at-login** behaviour are undocumented. They only matter for the extras and for reboots.

## Decisions for you

1. **Start at login:** should OrbStack start at login so the TV works after a reboot? Docker Desktop was manual.
2. **Memory cap:** 3072 MiB, or 2560 MiB?
3. **Version pinning:** pin by digest (recommended), or accept upgrades during the migration?
4. **Docker Desktop:** when to purge and uninstall it.
5. **IPv6:** may `enable_ipv6` be dropped if it causes trouble?

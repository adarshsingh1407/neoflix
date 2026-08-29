# Cross-Platform Support — Plan

Status: **implemented** — rollout steps 1-9 below (the actual code/doc
changes: `scripts/bootstrap.py`, `docker-compose.yml`, `.gitignore`,
`.env.example`, `credentials.env.example`, `SETUP.md`, `README.md`,
`DESIGN_DECISIONS.md` decision #13) are done on this branch. Step 10 (a
dedicated fresh end-to-end dry run covering every scenario in the Testing
plan below) hasn't been explicitly re-run against this exact diff —
this machine's stack has been in continuous real use throughout, which
exercises the happy path but not necessarily every wizard/edge-case
scenario listed. Still genuinely unverified either way: an actual run on
a Windows machine — see "What I cannot verify directly" below, which
remains accurate even post-implementation.

## Goal

Make neoflix's setup work equally well on macOS, Linux, and native Windows
(PowerShell/cmd.exe — not just WSL2), with **Docker and Python as the only
prerequisites**. No bash requirement, no manual OS-specific commands, no
"works on Windows if you use WSL2" caveat. As frictionless as the current
Mac-only flow, on every platform.

## Scope

In scope:
- `scripts/bootstrap.sh` and `scripts/bootstrap.py`
- `docker-compose.yml` (the `${PWD}`-based Ofelia labels)
- `.env.example`, `credentials.env.example`
- `SETUP.md`
- `scripts/requirements.txt`

Out of scope (see "Non-goals" at the bottom):
- Testing on an actual Windows machine (none available in this environment —
  flagged explicitly wherever a claim rests on documented behavior rather
  than something I've run)
- Rearchitecting bind-mounted host folders into Docker named volumes
- `scripts/ofelia/*.sh` — these run *inside* Linux containers regardless of
  host OS, so they're not a host-compatibility concern at all

## Current state: what's actually Unix-only today

| File | Line(s) | Issue |
|---|---|---|
| `scripts/bootstrap.sh` | 1 | `#!/usr/bin/env bash` — won't run on native Windows |
| `scripts/bootstrap.sh` | 23-26 | `$VENV_DIR/bin/pip`, `$VENV_DIR/bin/python` — Windows venvs use `Scripts\`, not `bin/` |
| `SETUP.md` | 67-68 | `id -u` / `id -g` for PUID/PGID — no Windows equivalent |
| `SETUP.md` | 73 | `readlink /etc/localtime | sed ...` for TZ — macOS/Linux only |
| `SETUP.md` | 200, 218, 237 | `ifconfig en0`, `ipconfig getifaddr en0` — macOS-only (despite `ipconfig` also existing on Windows, it's a different, incompatible command) |
| `docker-compose.yml` | 166, 172 | `${PWD}/scripts/ofelia:/scripts:ro` in Ofelia labels — `PWD` is a POSIX shell auto-export; PowerShell/cmd.exe don't set it as a real environment variable, so this would silently resolve blank on native Windows. **This is a regression from this session's own Ofelia-migration work**, not a pre-existing issue. |

## Design decisions

Each sub-problem below: the approach I'm recommending, and what else I
considered and why I ruled it out. Numbered so review comments can reference
them directly.

### D1. Kill the shell launcher — `bootstrap.py` becomes self-bootstrapping

**Recommended:** Delete `scripts/bootstrap.sh`. Move its job (check for
`.env`/`credentials.env`, create a venv if missing, install
`requirements.txt`, run the real logic) into the top of `bootstrap.py`
itself, using only stdlib (no `requests`/`yaml` needed for this part).

Mechanism:
```python
import os, sys, subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".bootstrap-venv"

def _in_venv():
    return sys.prefix != sys.base_prefix

def _venv_python():
    bindir = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return VENV_DIR / bindir / exe

def ensure_venv_and_reexec():
    if _in_venv():
        return
    if not VENV_DIR.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    vpy = str(_venv_python())
    subprocess.run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run([vpy, "-m", "pip", "install", "--quiet", "-r",
                     str(REPO_ROOT / "scripts" / "requirements.txt")], check=True)
    os.execv(vpy, [vpy, str(Path(__file__).resolve()), *sys.argv[1:]])

ensure_venv_and_reexec()

# only import requests/yaml AFTER the re-exec, since the outer/system
# Python doesn't have them installed yet
import requests
import yaml
...
```

Result: one command on every platform —
- macOS/Linux: `python3 scripts/bootstrap.py`
- Windows: `python scripts/bootstrap.py`

(Command name itself still differs by platform — see D-open-1 below on
why that's unavoidable and acceptable.)

**Execution order within `bootstrap.py` (gap found in review, now
specified):** the current module-level line
`DATA_ROOT = Path(ENV["DATA_ROOT"]).expanduser()` does a bare bracket
lookup that throws `KeyError` if `.env` doesn't have that key yet — which
is exactly the state a fresh clone is in before D7's wizard has run. Order
has to be:
1. `ensure_venv_and_reexec()` (stdlib only)
2. `import requests, yaml` (now available, post-reexec)
3. D7's wizard check — if `.env`/`credentials.env` are missing, prompt and
   write them *before* anything reads from `ENV`
4. Only then: `ENV = {**load_env_file(...), ...}` and everything derived
   from it (`DATA_ROOT`, `ADMIN_PASSWORD`, etc.)

This means step 3 can't stay implicit in module-level code the way `ENV`
parsing currently is — it needs to run as an explicit call before the
`ENV = {...}` line, not just "somewhere in the file."

Also: `bootstrap.py`'s own module docstring currently says *"Run via
scripts/bootstrap.sh, not directly"* — that instruction is reversed by D1
and must be corrected in the same change, or the file's own header
contradicts how it's meant to be invoked.

**Alternatives considered:**
- *Keep `bootstrap.sh`, add `bootstrap.ps1`* — two copies of the same
  orchestration logic (venv setup, error messages) to maintain in two
  languages forever. Rejected: violates this workspace's
  no-duplicated-logic default, and every future tweak has to be made twice
  and kept in sync.
- *Thin `.sh`/`.bat` shims that just call `python3 scripts/bootstrap.py`* —
  marginally friendlier for people who like typing `./bootstrap.sh`, but
  adds a second entry point with zero logic benefit. Rejected as
  unnecessary; can be revisited later as pure sugar if it's actually
  requested.

**Known wrinkle:** `os.execv` on Windows doesn't do a true in-place exec
(Windows has no such OS primitive) — Python emulates it by spawning the
target process, waiting on it, and exiting with its return code. Behaves
correctly for our purposes, just worth knowing it's not literally the same
mechanism as on POSIX.

### D2. PUID/PGID — auto-detect instead of manual `id -u`/`id -g`

**Recommended:** `bootstrap.py` computes these itself and writes them into
`.env`, the same way it already writes `JELLYFIN_API_KEY`:
- POSIX (`hasattr(os, "getuid")` is true): `os.getuid()` / `os.getgid()`
- Windows: no equivalent concept exists (NTFS doesn't use uid/gid). Default
  to `1000`/`1000` — linuxserver.io's own documented default — since Docker
  Desktop's backend on Windows translates bind-mount permissions itself;
  PUID/PGID stop being load-bearing there.

Removes this from SETUP.md's manual steps entirely.

**Write semantics (gap found in review, now specified): only write PUID/
PGID if the key is absent from `.env` — never overwrite an existing
value.** Unlike `JELLYFIN_API_KEY` (D4's `NEOFLIX_REPO_ROOT` is the same
category), PUID/PGID are *preferences with a sensible auto-detectable
default*, not derived/generated state. A user who manually set `PUID=999`
to match a specific NAS/permissions setup would have it silently reverted
on every re-run if this followed the "always overwrite" pattern. Detect
only fills a genuinely missing key.

**Alternatives considered:**
- *Leave manual, just document the Windows non-equivalent* — keeps a step
  in the guide that doesn't need to exist and keeps SETUP.md forked by
  platform for no reason. Rejected.

### D3. TZ — auto-detect instead of manual `readlink`

**Recommended:** use the `tzlocal` package (`tzlocal.get_localzone_name()`),
which handles this correctly cross-platform:
- macOS/Linux: reads `/etc/localtime` (same thing `readlink` was doing by
  hand)
- Windows: reads the registry and maps it to an IANA zone name via a
  bundled mapping table. On Windows this needs the IANA tz database itself,
  which Windows doesn't ship — `tzlocal`'s own packaging already declares a
  conditional dependency (`tzdata; platform_system == "Windows"`), so `pip
  install tzlocal` on Windows pulls that in automatically. No extra work on
  our side beyond adding `tzlocal` to `requirements.txt`.
- Fallback: if detection throws for any reason, fall back to `Etc/UTC` and
  print a warning (matches the existing `warn()` pattern used everywhere
  else in the script), rather than failing the whole run.
- Same write-only-if-absent semantics as D2's PUID/PGID, for the same
  reason — a manually corrected `TZ` shouldn't get clobbered by
  re-detection on a later re-run.

**Alternatives considered:**
- *Ask the user to pick from a short list at the prompt* — zero new
  dependencies, but reintroduces a manual step the current Mac-only flow
  doesn't have either. Rejected — it's a downgrade for every platform, not
  just a Windows accommodation.
- *Read `time.tzname` / stdlib only* — stdlib doesn't expose a reliable
  cross-platform IANA-zone lookup; `time.tzname` gives OS-locale-dependent
  abbreviations (e.g. "EST"), not something Docker containers/apps can use
  as a `TZ` value. Rejected as insufficient.

### D4. Ofelia's `${PWD}` labels → a value `bootstrap.py` writes itself

**Recommended:** `bootstrap.py` writes the resolved repo path into `.env` as
a new variable, e.g. `NEOFLIX_REPO_ROOT=<REPO_ROOT.as_posix()>`, unconditionally,
**every run** (deliberately the opposite write semantics from D2/D3's
"only if absent" — this value is derived, not a preference, and there's no
legitimate reason a user would want a stale one: if the repo ever moves, the
old path silently breaks the Ofelia mounts, same failure mode as the
`${PWD}` bug it's replacing), before `docker compose up -d` is called.
`docker-compose.yml`'s
Ofelia labels change from `${PWD}/scripts/ofelia:/scripts:ro` to
`${NEOFLIX_REPO_ROOT}/scripts/ofelia:/scripts:ro`.

Why this fixes it cleanly: Docker Compose reads a project-directory `.env`
file itself for variable substitution — this is completely independent of
what the invoking shell does or doesn't export, so it behaves identically
under bash, zsh, PowerShell, and cmd.exe. Using `.as_posix()` also means the
value is always forward-slash, so it never breaks JSON-array parsing inside
the Ofelia volume label (which is a quoted JSON string; a raw Windows
backslash path landing in there would be interpreted as escape sequences).

**Alternatives considered:**
- *Tell Windows users to `cd` into the repo a specific way, document a
  workaround* — doesn't actually fix cross-shell portability, just papers
  over it with instructions. Rejected.
- *Use a relative path in the label instead* — already established in this
  session (via `docker compose config`) that Compose does **not** resolve
  relative paths inside label string values, only inside the top-level
  `volumes:` YAML section. Not viable.

**Migration gap found in review — highest severity item in this whole
plan:** the `.env` on *this machine's currently-running instance* predates
`NEOFLIX_REPO_ROOT` entirely. If this change ships and someone runs `docker
compose up -d` directly (a normal thing to do for routine restarts —
doesn't require re-running `bootstrap.py`) without having run the new
`bootstrap.py` at least once first, `${NEOFLIX_REPO_ROOT}` resolves blank
and the Ofelia mounts break — the exact same failure mode as the original
`${PWD}` bug, just now hitting an existing deployment instead of a fresh
one. **The plan needs an explicit migration step**, not just doc updates:
either (a) a note in the PR/changelog telling existing users to run
`bootstrap.py` once after pulling this change, before their next `docker
compose up`, or (b) have `docker-compose.yml`'s Ofelia labels tolerate a
missing value more gracefully (not really fixable at the Compose level —
blank interpolation is blank interpolation). Recommend (a): call it out
prominently in the PR description and in `DESIGN_DECISIONS.md`'s decision
#13 update, the same way decision #13 already documents other
`bootstrap.py`-must-run-once-more situations.

### D5. `DATA_ROOT` path format safety net

Not currently broken (pathlib handles both slash styles natively on
Windows), but it feeds into the same JSON-array Ofelia label the `${PWD}`
bug did, so a backslash `DATA_ROOT` pasted into `.env` on Windows is a
latent version of the same problem.

**Recommended:**
- `.env.example` gets a Windows example line showing forward slashes
  (`C:/Users/YourName/neoflix-data`), alongside the existing Mac example.
- `bootstrap.py`'s `load_env_file`/write-back path normalizes `DATA_ROOT` to
  `.as_posix()` form if it finds backslashes, and rewrites `.env` — the same
  safety-net pattern already used for `JELLYFIN_API_KEY`.
- **Hardening gaps found in review:**
  - All `.env`/`credentials.env` writes (existing `JELLYFIN_API_KEY`
    write-back included, plus every new one this plan adds) should use
    `path.open("w", newline="\n")` rather than `Path.write_text()`'s
    platform-default newline translation, so these files stay consistently
    LF-terminated on Windows instead of picking up `\r\n`. Not currently a
    parse-breaking bug (`load_env_file`'s `.strip()` absorbs a stray `\r`
    on read), but the regex-based write-back helpers (`re.sub(...".*$"...,
    re.MULTILINE)`) aren't `\r`-safe against text they didn't write
    themselves, so it's worth closing rather than leaving latent.
  - `create_folders()`'s `mkdir(parents=True, exist_ok=True)` calls have no
    error handling today — an invalid or unwritable `DATA_ROOT` throws a
    raw traceback. This was a low-probability edge case when `DATA_ROOT`
    only ever came from hand-editing `.env.example`; it's a more reachable
    one now that D7's wizard actively invites a freshly typed path. Wrap in
    a `try/except OSError` with a clear message ("Couldn't create `<path>`
    — check the path is valid and writable") rather than a bare traceback.

### D6. Alternative considered and rejected: bind mounts → named volumes

Docker named volumes would sidestep DATA_ROOT path-format issues and
PUID/PGID permission mapping entirely (Docker manages permissions and
storage internally). Rejected for this plan: it's a much larger blast
radius — it contradicts `DESIGN_DECISIONS.md` decision #1 (a browsable host
media folder was an explicit, deliberate choice), would change how every
existing user accesses their media library files directly on disk, and
isn't necessary to hit the "Docker + Python only" goal. Noted here so it's
on record as considered, not overlooked.

### D7. Interactive first-run setup wizard

Even after D1-D5, a first-time user still has to open `.env` and
`credentials.env` in a text editor, know which of ~6 keys actually matter,
and understand each from a comment. That's the last real chunk of manual
friction left in the flow.

**Recommended:** when `bootstrap.py` runs and `.env` and/or
`credentials.env` don't exist yet, prompt for the values interactively
instead of erroring out and pointing at the `.example` templates, then
write the files itself. Stdlib only — `input()` for plain values,
`getpass.getpass()` for passwords (same module `pip`/`git` use for masked
password prompts, already correct cross-platform on Windows/macOS/Linux).

Flow:
- `DATA_ROOT` — prompt with a default of `Path.home() / "neoflix-data"`
  pre-filled (hit enter to accept, or type an alternate path). **Validation
  added in review:** reject (re-prompt) if the resolved path is inside
  `REPO_ROOT` — SETUP.md already documents this as a hard requirement
  ("outside this repo folder ... never accidentally gets swept up into
  git") but nothing currently enforces it. The wizard is the one place
  actively inviting a typed path, so it's the right place to catch the
  mistake instead of just documenting around it.
- `PUID` / `PGID` / `TZ` — **no prompt at all.** D2/D3 already auto-detect
  these; the wizard just skips them entirely, which is strictly better than
  asking.
- `ADMIN_USERNAME` — prompt, default `admin`
- `ADMIN_PASSWORD` — `getpass.getpass()`, asked twice to confirm (no visual
  echo, so a typo would otherwise go unnoticed until a service rejects it).
  This also structurally removes the current `changeme`-placeholder
  footgun, which today only fails at runtime with an error message.
- OpenSubtitles — single y/n gate ("Set up automatic subtitles? [y/N]").
  Only asks for the username/password pair if yes; answering no leaves both
  blank, matching today's documented "leave blank to skip" behavior exactly.
- Writes the answers straight into `.env`/`credentials.env` in the same
  format `load_env_file`/the existing write-back code already reads, so
  every downstream step is unaffected — the wizard only changes *how* those
  two files get created, not what's in them or how the rest of the script
  consumes them.

**Non-interactive safety:** check `sys.stdin.isatty()` before prompting. If
false (piped input, CI, no TTY), skip straight to today's existing
behavior — exit with the "missing `.env`, copy the `.example` template"
error — rather than hanging forever waiting for input that will never
arrive. This also means the file-based path stays fully alive for anyone
who wants to script/version-control their answers or reprovision a box
headlessly; the wizard is additive, not a replacement for
`.env.example`/`credentials.env.example`.

**Interrupt handling and write atomicity (gap found in review):** collect
all answers in memory first, write both files only once every prompt has
succeeded — not incrementally per-field. Catch `KeyboardInterrupt` (Ctrl-C)
and `EOFError` (Ctrl-D / closed stdin mid-prompt) around the whole prompt
sequence and exit cleanly ("Setup cancelled, no files were written.")
rather than a raw traceback or a half-written `.env`/`credentials.env` that
would confuse the next run's "is this file missing or present" check.

**Trigger-condition gap found in review — resolved: extend the trigger.**
As originally specified, the wizard only fired when a file was *absent* —
it didn't catch `credentials.env` existing but still holding the literal
`changeme` placeholder (e.g. someone copied the template, as SETUP.md's old
flow instructed, and didn't finish editing it). **Decision: the wizard now
also fires when `credentials.env` exists but `ADMIN_PASSWORD` is missing or
still equals `changeme`** — same `sys.stdin.isatty()` gate applies, so a
non-interactive run still falls back to the existing
`sys.exit("...set a real ADMIN_PASSWORD...")` error rather than hanging.
When this path fires with the file already present, only prompt for the
credentials fields (`ADMIN_USERNAME`/`ADMIN_PASSWORD`/OpenSubtitles) and
rewrite just those keys in the existing file — don't touch `.env` or
re-ask anything already answered. This is the same "only touch what
actually needs fixing" principle as D-open-4's resolution, just triggered
by an invalid value instead of a missing file.

**Alternatives considered:**
- *Always require the `.example` → hand-edit flow, just document it
  better* — cheapest option, but leaves the actual friction (open two
  files, figure out which keys matter) completely untouched. Rejected —
  doesn't move the needle on the stated goal.
- *A separate `scripts/setup_wizard.py` / dedicated CLI entry point* — one
  more thing to run, one more command to document, and it would need to
  duplicate `bootstrap.py`'s `.env`/`credentials.env` parsing logic (or
  import from it, at which point it's not really separate). Rejected in
  favor of folding it into `bootstrap.py`'s existing "check for missing
  files" branch, which already exists and already runs first.
- *Third-party CLI/prompt library (e.g. `questionary`, `click`)* — nicer
  UX (arrow-key selection, inline validation, colored output) but a new
  dependency for something `input()`/`getpass` already do adequately.
  Rejected under this workspace's "minimize dependencies" default — revisit
  only if plain `input()` proves genuinely painful in practice.

### D8. Docker-availability preflight check (added in review)

Today, `docker_compose_up()` runs `subprocess.run(["docker", "compose",
"up", "-d"], ...)` and checks the return code — but if `docker` itself
isn't on `PATH` at all, `subprocess.run` raises an uncaught
`FileNotFoundError`, producing a raw Python traceback instead of a
sensible message. This is a pre-existing gap on every platform, but it's
disproportionately likely to be the *first* thing a Windows user hits:
Docker Desktop installed but not yet started, or `PATH` not refreshed in
the current shell after install, are common first-run states.

**Recommended:** at the very start of `main()`, before anything else, check
`shutil.which("docker")` and run `docker info` (cheap, fails fast if the
daemon isn't running) — on either failure, exit with a clear message
("Docker not found on PATH" / "Docker is installed but not running — start
Docker Desktop and try again") instead of letting the failure surface three
steps later as an unrelated traceback.

## File-by-file change list

| File | Change |
|---|---|
| `scripts/bootstrap.sh` | **Deleted.** |
| `scripts/bootstrap.py` | Add `ensure_venv_and_reexec()` (D1) at the top, before third-party imports, and fix the module docstring. Add D8's Docker preflight check at the very start of `main()`. Add PUID/PGID/TZ auto-detection + write-only-if-absent `.env` write-back (D2, D3) as a new first step in `main()`, before `create_folders()`. Add `NEOFLIX_REPO_ROOT` always-overwrite write-back (D4) at the very start of `main()`, before `docker_compose_up()`. Add `DATA_ROOT` posix-normalization + `mkdir` error handling (D5). Add the interactive wizard (D7): fires when `.env`/`credentials.env` are missing, *or* when `credentials.env` exists but `ADMIN_PASSWORD` is missing/still `changeme` — gated on `sys.stdin.isatty()`, atomic writes, `DATA_ROOT`-inside-repo validation, only prompts for whichever piece is actually missing/invalid. |
| `scripts/requirements.txt` | Add `tzlocal`. |
| `docker-compose.yml` | Ofelia labels: `${PWD}` → `${NEOFLIX_REPO_ROOT}` (2 occurrences, lines 166 and 172). |
| `.env.example` / `credentials.env.example` | Remove `id -u`/`id -g`/`readlink` comments (values are now auto-written). Add a Windows-style `DATA_ROOT` example line. Document `NEOFLIX_REPO_ROOT` as script-managed, same framing as the existing `JELLYFIN_API_KEY` comment. Both files stay as-is otherwise — still the source of truth for the non-interactive/scripted path (D7). |
| `SETUP.md` | Step 3 shrinks: PUID/PGID/TZ instructions removed, `DATA_ROOT` stays. Steps 3-4 get reframed as "either answer the prompts, or fill these files in by hand" now that D7 makes the wizard the default first-run path. Step 5 command becomes platform-conditional (`python3 ...` / `python ...`). LAN-IP and MAC-address sections get a per-OS table instead of Mac-only commands. Add a note that password rotation on already-created accounts isn't automated (surfaced under D-open-5). Prerequisites stays generic per D-open-2 (resolved: no change needed there). |
| `README.md` | **Missing from the original plan — added in review.** "Running it" section (lines 89-101) references `scripts/bootstrap.sh` twice and shows `cp .env.example .env # adjust PUID/PGID/TZ/DATA_ROOT if needed`, which becomes wrong once D2/D3 auto-detect those three. Rewrite to match the new `python3/python scripts/bootstrap.py` command and drop the PUID/PGID/TZ mention. |
| `.gitignore` | **Missing from the original plan — added in review.** Currently only excludes `.env`, `credentials.env`, `.DS_Store` — `.bootstrap-venv/` was never added, a pre-existing gap. Add it now, since this plan makes that directory more central to the flow (created on literally every first run, on every platform). |
| `DESIGN_DECISIONS.md` | **Original plan said "add a new entry" — review found the *existing* decision #13 body also needs correcting, not just supplementing.** It currently names `scripts/bootstrap.sh` twice and its "Follow-up, closed" note describes the exact `${PWD}` mechanism this plan removes — left as-is, the doc would contradict the code. Update decision #13's existing text in place (script name, `${PWD}`→`NEOFLIX_REPO_ROOT`, PUID/GID/TZ now auto-detected, wizard added), and prominently note the migration requirement from D4 (existing installs need one `bootstrap.py` run post-upgrade before their next plain `docker compose up -d`). |

## New dependency: `tzlocal`

Pure Python, no compiled/native extensions. Pulls in `tzdata` on Windows
only (via `pip`'s own environment markers, no action needed from us).
Justification per this workspace's "minimize dependencies" default: there's
no stdlib equivalent that reliably maps the local OS timezone to an IANA
zone name on both Windows and POSIX — `time.tzname` isn't sufficient (see
D3) — so this clears the "clear win over native/existing solutions" bar.

## Testing plan

What I can actually verify (macOS, the only platform available here):
- Full dry run of the rewritten `bootstrap.py`, same rig used for the
  original PR (scratch `DATA_ROOT`, isolated container/network names,
  torn down after) — confirms the venv self-bootstrap path, PUID/GID/TZ
  auto-detection, and `NEOFLIX_REPO_ROOT` all work correctly on macOS and
  that nothing regresses versus the current `bootstrap.sh` flow.
- `docker compose config` to confirm `${NEOFLIX_REPO_ROOT}` resolves
  correctly and that the Ofelia labels still parse as valid JSON.
- Deliberately setting a backslash-containing `DATA_ROOT` in a scratch
  `.env` to confirm the D5 normalization actually rewrites it.
- **Added from gap analysis:**
  - *Upgrade-path test*: start from a scratch `.env` that looks like a
    pre-this-plan file (no `NEOFLIX_REPO_ROOT`, manually filled PUID/PGID/
    TZ) and confirm one `bootstrap.py` run adds `NEOFLIX_REPO_ROOT` and
    leaves the manual PUID/PGID/TZ values untouched — directly verifies
    both the D4 migration concern and D2/D3's write-only-if-absent fix.
  - *Re-run preservation test*: run `bootstrap.py` twice in a row against
    the same scratch setup, confirm PUID/PGID/TZ are byte-identical after
    both runs (nothing got silently reverted) while `NEOFLIX_REPO_ROOT`
    would still update correctly if the scratch dir were moved between
    runs.
  - *Wizard interrupt test*: start the wizard, Ctrl-C partway through,
    confirm neither `.env` nor `credentials.env` was created (or, if one
    already existed, that it's untouched) rather than left half-written.
  - *`DATA_ROOT`-inside-repo rejection test*: answer the wizard's prompt
    with a path inside the scratch repo checkout, confirm it's rejected
    and re-prompts rather than silently accepted.
  - *Missing-Docker test*: temporarily rename/hide `docker` from `PATH` in
    the test shell, confirm D8 produces the intended clear message instead
    of a raw traceback.
  - *Partial-file test*: delete only `credentials.env` from an otherwise
    complete scratch setup, confirm the wizard prompts only for that file
    and `.env` is untouched (D-open-4's resolution).
  - *Placeholder-trigger test*: leave `credentials.env` present with
    `ADMIN_PASSWORD=changeme` (i.e. the file exists but was never actually
    filled in), confirm the wizard now fires for just the credentials
    fields, `.env` is untouched, and a non-interactive run (no TTY) still
    falls back to the existing `sys.exit` error rather than hanging.

What I **cannot** verify directly (no Windows machine in this environment)
— flagged as assumptions resting on documented Windows/Python behavior,
not something I've run:
- That `os.execv`'s Windows emulation behaves as expected end-to-end
- That `tzlocal` correctly resolves the local zone via the Windows registry
- That Docker Desktop for Windows accepts the forward-slash `DATA_ROOT`/
  `NEOFLIX_REPO_ROOT` paths in bind mounts the way documented
- General Windows Docker Desktop behavior (first-run firewall prompts,
  default backend, etc.)

If you have access to a Windows machine (or a Windows VM) at review or
implementation time, that's the one gap worth closing with a real run
before calling this "done" rather than "should work."

## Rollout sequencing

1. `scripts/requirements.txt` — add `tzlocal` (no behavior change yet)
2. `.gitignore` — add `.bootstrap-venv/` (independent, do it early so
   nothing accidentally gets staged during development of the rest)
3. `scripts/bootstrap.py` — D1 (self-bootstrap + corrected docstring), D8
   (Docker preflight), D4 (`NEOFLIX_REPO_ROOT`) together — D8 and D4 both
   need to run at the very start of `main()` regardless, so no reason to
   split them into separate passes
4. `scripts/bootstrap.py` — D2 + D3 (PUID/GID/TZ auto-detect, write-only-
   if-absent)
5. `scripts/bootstrap.py` — D5 (`DATA_ROOT` normalization + `mkdir`
   error handling + LF-newline write hardening)
6. `docker-compose.yml` — `${PWD}` → `${NEOFLIX_REPO_ROOT}`
7. Delete `scripts/bootstrap.sh`
8. `scripts/bootstrap.py` — D7 (interactive wizard: prompts, atomic
   write/interrupt handling, `DATA_ROOT`-inside-repo validation), last,
   since it only changes *how* `.env`/`credentials.env` get created and
   every other step should already be working against those files by this
   point
9. `.env.example`, `credentials.env.example`, `SETUP.md`, `README.md`,
   `DESIGN_DECISIONS.md` doc updates (including decision #13's corrected
   existing text and the migration note from D4)
10. Full dry run on macOS, covering every scenario the gap analysis
    surfaced (see updated Testing plan below)

Each step is independently small and testable; doing D1+D4+D8 before D2/D3
means there's a working self-bootstrapping script to test against earlier
rather than one big-bang change. D7 goes last since it's purely additive on
top of everything else and easiest to validate once the rest is solid.

## Resolved decisions (from review)

All five open questions were discussed and settled before implementation:

- **D-open-1** — *Resolved: platform-conditional command in the docs is
  fine.* SETUP.md documents both `python3 scripts/bootstrap.py` (macOS/
  Linux) and `python scripts/bootstrap.py` (Windows) side by side. No
  wrapper script.
- **D-open-2** — *Resolved: Prerequisites stays generic.* No
  Windows-specific Docker Desktop callout — nothing in this plan actually
  depends on a particular backend anymore.
- **D-open-3** — *Resolved: confirmed, nothing else to change.*
  `credentials.env`/`credentials.env.example` need no changes beyond D7
  itself.
- **D-open-4** — *Resolved: prompt only for the file that's actually
  missing, leave any existing file untouched.* Matches the state-checking
  pattern used everywhere else in `bootstrap.py`, and avoids the risk of a
  re-prompted `DATA_ROOT` drifting from the value already-running
  containers are using.
- **D-open-5** — *Resolved: delete-to-redo, no new flag.* To rerun the
  wizard for one file, delete it and re-run the script — D-open-4's
  missing-file check handles the rest. `bootstrap.py` doesn't parse any
  command-line arguments today, and this doesn't create a strong enough
  reason to start.
  **Important caveat surfaced during this discussion, worth documenting in
  SETUP.md alongside the existing "two things are never automated"
  callout**: regenerating `credentials.env` with a new `ADMIN_PASSWORD` and
  re-running does **not** rotate the password on already-created accounts
  (qBittorrent/Jellyfin/Uptime Kuma) — `setup_qbittorrent`/`setup_jellyfin`/
  `setup_uptime_kuma` only handle initial account creation today, not
  detecting/updating a changed password on an existing account. Password
  rotation is out of scope for this plan; changing an already-set password
  still has to be done by hand, in each app's own UI.

## Non-goals

- Testing on a real Windows machine myself (flagged throughout — happy to
  hand you a checklist to run through if you have access to one)
- Moving off bind-mounted host folders to named volumes (D6)
- Touching `scripts/ofelia/*.sh` — these run inside Linux containers, not
  on the host, regardless of host OS
- Any change to the actual service configuration/behavior — this is
  entirely about the setup/bootstrap tooling

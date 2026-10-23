# bookshelf-decrypt

Personal-use tool for downloading audiobooks you've bought on Audible and
converting them from AAX/AAXC to plain MP3, without going through a
third-party conversion website.

It's a thin wrapper around two well-established tools:

- [**audible-cli**](https://github.com/mkb79/audible-cli) handles
  authentication and downloading from *your own* library.
- **ffmpeg** handles the decrypt + transcode step, using *your own*
  account's activation bytes.

Everything runs locally. Nothing is ever uploaded to a third-party site, and
your Audible credentials never pass through this script — the OAuth login
happens entirely inside `audible-cli`.

## Table of contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Setup](#setup)
- [One-time login](#one-time-login)
- [Usage](#usage)
- [Command reference](#command-reference)
- [File layout](#file-layout)
- [Logging out / switching accounts](#logging-out--switching-accounts)
- [Troubleshooting](#troubleshooting)
- [Security & privacy notes](#security--privacy-notes)
- [License](#license)

## Why this exists

Activation bytes are derived from your own Audible account's device
registration — they're what let ffmpeg decrypt an AAX file you already
bought, the same way a region-free DVD rip doesn't require defeating someone
else's copy of a disc. This tool keeps that whole process local instead of
trusting a random website with your Audible credentials.

This is a **personal-use tool**. It only operates on books already present
in your own library, authenticated as you, using bytes derived from your own
device registration. It does not circumvent anyone else's DRM, and it is not
intended for redistributing audiobooks you don't own.

## How it works

```
┌────────────┐   audible-cli    ┌──────────────┐    ffmpeg     ┌──────────┐
│  Audible    │ ───────────────▶│  downloads/  │ ─────────────▶│   mp3/   │
│  (your acct)│  download --aax │  *.aax/aaxc  │ decrypt+encode │  *.mp3   │
└────────────┘                  └──────────────┘                └──────────┘
```

1. `login` runs `audible quickstart`, audible-cli's own one-time OAuth
   flow. It stores a token on disk (not in this repo) and never touches
   this script.
2. `download` calls `audible download --aax` to pull the encrypted
   `.aax`/`.aaxc` files for your library into `./downloads`.
3. `activation-bytes` asks audible-cli, once, for the decryption key tied to
   your account/device, and caches it locally in `.activation_bytes` so
   it isn't re-fetched on every conversion.
4. `convert` runs `ffmpeg` twice per file:
   - First pass transcodes audio only (so ffmpeg's progress reporting,
     which otherwise locks onto the single embedded cover-art "frame" and
     gets stuck at 0%, tracks correctly).
   - Second pass is a fast stream-copy remux that reattaches cover art,
     metadata and chapter markers from the original file.
   Output lands in `./mp3`.

## Setup

### Option A: Docker (no local Python needed)

Everything (Python, `audible-cli`, `ffmpeg`) is baked into the image. Build
it once:

```bash
docker compose build
```

Then run commands through `docker compose run --rm audible-mp3 <command>`
instead of `python audible_mp3.py <command>` (see [Usage](#usage) below).
Downloaded `.aax` files land in `./downloads`, converted files in `./mp3`,
and your audible-cli login is kept in `./audible_config` — all on your host
machine, mounted into the container (see [compose.yaml](compose.yaml)), so
nothing is lost when the container exits.

Rebuild the image (`docker compose build`) any time `audible_mp3.py`,
`requirements.txt`, or the `Dockerfile` changes.

### Option B: Local Python

Requires Python 3.9+.

```bash
pip install -r requirements.txt
```

You also need `ffmpeg` (and `ffprobe`, which ships alongside it) on your
`PATH`. Download it from [ffmpeg.org](https://ffmpeg.org/download.html), or
install via a package manager (e.g. `choco install ffmpeg` on Windows,
`brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu).

## One-time login

```bash
python audible_mp3.py login
# or, with Docker:
docker compose run --rm audible-mp3 login
```

This runs `audible quickstart`, which walks you through audible-cli's OAuth
flow:

1. It prints a URL — open it in a browser and log in to your Amazon/Audible
   account (solving any CAPTCHA/2FA Amazon asks for).
2. After login, Amazon redirects to a URL that won't actually load (that's
   expected) — copy that URL from your browser's address bar.
3. Paste it back into the terminal when `audible quickstart` asks for it.
4. You'll also be asked for your marketplace/country code (e.g. `us`,
   `uk`, `de`) — pick the one your Audible account is registered in.

Credentials/tokens are stored by audible-cli itself (under `~/.audible` by
default, or `./audible_config` when using Docker), **not in this repo**. See
[Security & privacy notes](#security--privacy-notes).

This only needs to be done once per account/device — `audible_mp3.py`
reuses the saved token for every later command.

## Usage

```bash
# Download every purchased title as .aax into ./downloads
python audible_mp3.py download

# Or just one title by ASIN (find it on the book's Audible product page)
python audible_mp3.py download --asin B0XXXXXXXX

# Convert everything in ./downloads to MP3 in ./mp3
python audible_mp3.py convert

# Or do both in one go
python audible_mp3.py sync

# Just want the activation bytes (e.g. to use with ffmpeg yourself)?
python audible_mp3.py activation-bytes
```

With Docker, prefix each command with `docker compose run --rm
audible-mp3` instead of `python audible_mp3.py`, e.g.
`docker compose run --rm audible-mp3 sync`.

## Command reference

| Command | What it does | Options |
|---|---|---|
| `login` | Runs `audible quickstart` (one-time OAuth setup). | — |
| `logout` | Clears your audible-cli login and cached activation bytes, and double-checks with a real audible-cli request that no credentials remain, so `login` can be run fresh. See [below](#logging-out--switching-accounts). | — |
| `activation-bytes` | Prints your account's activation bytes, fetching and caching them on first use. | `--refresh` — ignore the cache and re-fetch from audible-cli. |
| `download` | Downloads purchased `.aax` files into `./downloads`. | `--asin ASIN` — only this title (omit to download everything); `--jobs N` — simultaneous downloads (default `3`); `--ignore-errors` — skip titles that fail instead of aborting the whole batch. |
| `convert` | Converts every `.aax`/`.aaxc` file in `./downloads` to MP3 in `./mp3`, with a progress bar per file. | `--force` — re-convert even if an MP3 with that name already exists (default: skip). |
| `sync` | Runs `download` then `convert` back to back. | All of `download`'s and `convert`'s options (`--asin`, `--jobs`, `--ignore-errors`, `--force`). |

Run `python audible_mp3.py <command> --help` (or
`docker compose run --rm audible-mp3 <command> --help`) for the same
information from argparse directly.

## File layout

```
bookshelf-decrypt/
├── audible_mp3.py        # the whole tool — a single-file CLI
├── requirements.txt       # audible-cli, tqdm
├── Dockerfile             # python + ffmpeg + audible-cli, baked in
├── compose.yaml           # bind-mounts downloads/, mp3/, audible_config/
├── downloads/             # .aax/.aaxc files land here (gitignored)
├── mp3/                   # converted .mp3 files land here (gitignored)
├── audible_config/        # audible-cli's login, Docker only (gitignored)
└── .activation_bytes      # cached activation bytes (gitignored)
```

None of the gitignored paths above are ever meant to be committed — see
[.gitignore](.gitignore), which also blocks common audio extensions
(`*.aax`, `*.aaxc`, `*.mp3`, `*.m4b`, …) anywhere in the repo as a safety
net, in case a file ends up outside those directories.

## Logging out / switching accounts

```bash
python audible_mp3.py logout
# or, with Docker:
docker compose run --rm audible-mp3 logout
```

`audible quickstart` (used by `login`) refuses to run again while a config
already exists, so use `logout` first if you want to re-authenticate or
switch to a different Audible account. It:

1. Deletes the contents of audible-cli's config directory (`~/.audible`,
   `%LOCALAPPDATA%\audible` on Windows, or `./audible_config` under Docker
   — whichever `audible_config_dir()` resolves to, honoring an
   `AUDIBLE_CONFIG_DIR` override if you've set one).
2. Deletes the cached `.activation_bytes` file (it's tied to the login
   that just got cleared).
3. Re-reads both locations from disk to confirm nothing was left behind,
   rather than assuming the deletes succeeded.
4. Makes a real request (`audible library list`) to audible-cli itself, so
   you get independent confirmation — not just "files are gone" but
   "audible-cli genuinely has no working credentials anymore".

If you run it when you're already logged out, it tells you there was
nothing to remove instead of pretending it did something.

## Troubleshooting

- **`audible quickstart` says a config already exists.** Run `logout`
  first (see above), then `login` again.
- **`ffmpeg: command not found` (local Python install).** Install ffmpeg
  and make sure it's on your `PATH`; restart your terminal afterward. Or
  switch to the Docker setup, which bundles it.
- **A title fails to download.** Re-run with `--ignore-errors` to skip it
  and continue with the rest of the batch; investigate the specific title
  with `python audible_mp3.py download --asin <that ASIN>` to see the full
  error.
- **Conversion progress bar is stuck at 0%.** This is the exact problem
  `convert_one()` works around (see [How it works](#how-it-works)); if you
  still see it, the file's audio stream may not be mapped the way the
  script expects — open an issue with the `ffprobe -show_streams` output
  for that file.
- **Activation bytes seem stale / decryption fails after re-registering a
  device.** Run `python audible_mp3.py activation-bytes --refresh`.
- **Windows: `python` isn't recognized.** Use the Docker setup, or install
  Python from [python.org](https://www.python.org/) and make sure "Add
  python.exe to PATH" was checked during install.

## Security & privacy notes

- Your Audible password is never seen by this script — the OAuth flow in
  `login` is entirely handled by `audible-cli`, in your browser.
- Login tokens and activation bytes live outside the repo (see
  [File layout](#file-layout)) and are gitignored; don't commit
  `audible_config/` or `.activation_bytes` even if you add other
  exceptions to `.gitignore` later.
- Downloaded/converted audio files are yours, purchased, and gitignored —
  this repo is meant to hold only the tool, never your library.

## License

No license file is included yet, which means default copyright applies —
all rights reserved by the author. Open an issue if you'd like a specific
open-source license added.

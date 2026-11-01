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
  - [Output format: MP3 vs M4B](#output-format-mp3-vs-m4b)
  - [Large libraries / limited disk space](#large-libraries--limited-disk-space)
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
   - First pass handles audio only (so ffmpeg's progress reporting, which
     otherwise locks onto the single embedded cover-art "frame" and gets
     stuck at 0%, tracks correctly). For MP3 (the default, `--format mp3`)
     this transcodes to `libmp3lame`; for `--format m4b` it's a lossless
     copy, since Audible's own audio is already AAC.
   - Second pass is a fast stream-copy remux that reattaches cover art,
     metadata and chapter markers from the original file.
   Output lands in `./mp3` either way (see [Output format: MP3 vs
   M4B](#output-format-mp3-vs-m4b)).

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

# Or every title whose name starts with a given letter
python audible_mp3.py download --starts-with A

# Convert everything in ./downloads to MP3 in ./mp3
python audible_mp3.py convert

# Or convert to M4B instead (lossless, keeps chapters as their own markers)
python audible_mp3.py convert --format m4b

# Or do both in one go
python audible_mp3.py sync

# Just want the activation bytes (e.g. to use with ffmpeg yourself)?
python audible_mp3.py activation-bytes

# Large library, small disk: download a few at a time, converting (and
# deleting) each one the moment its own download finishes, and safe to
# stop/re-run.
python audible_mp3.py batch --jobs 3
```

With Docker, prefix each command with `docker compose run --rm
audible-mp3` instead of `python audible_mp3.py`, e.g.
`docker compose run --rm audible-mp3 sync`.

### Output format: MP3 vs M4B

`convert`, `sync` and `batch` all take `--format mp3` (the default) or
`--format m4b`:

- **mp3** re-encodes the audio through `libmp3lame`. Slower (it's a real
  transcode) and technically lossy, but MP3 plays absolutely everywhere.
- **m4b** is a lossless remux — Audible's own audio is already AAC, so
  this just copies it into an `.m4b` container instead of re-encoding it.
  Faster, smaller/same size for the same quality, and M4B's chapter atoms
  are what most audiobook apps (e.g. a phone's Books/Audiobooks app) use
  for chapter navigation and remembering playback position per book. Needs
  a player that understands M4B, which is nearly everything built for
  audiobooks/podcasts — just not universal like MP3.

Pick per-command, e.g. `python audible_mp3.py batch --format m4b`.

### Large libraries / limited disk space

`sync` (and `download`) pull everything down first and convert after, so
for a big library the `.aax` files for the *whole* batch have to fit on
disk at once — not realistic for, say, 840 books on a laptop drive.

`batch` instead keeps at most `--jobs` downloads in flight at once, and as
soon as any *one* of them finishes, converts that book and deletes its
`.aax` immediately — it never waits for the others still downloading
before converting a book that's already done. Progress is tracked in
`downloads/.sync_progress.json`, so if it's interrupted (Ctrl-C, a reboot,
running out of space anyway) you can just re-run the exact same command and
it skips everything already converted and picks up where it stopped —
no need to track which books are done yourself. A title whose download
fails is skipped (logged at the end) and simply retried on the next run,
instead of aborting the whole thing.

```bash
# 3 downloads in flight at once, refusing to start a new one once
# free space drops under 5 GB
python audible_mp3.py batch --jobs 3 --min-free-gb 5

# Only the "A" titles this round
python audible_mp3.py batch --starts-with A

# Keep the original .aax files around instead of deleting them after conversion
python audible_mp3.py batch --keep-source
```

Tune `--jobs` to how much headroom you actually have: if a book's `.aax`
plus its converted `.mp3` run to ~1 GB combined, `--jobs 3` needs roughly
3 GB free at any one time, regardless of how many hundreds of books are
left overall (an in-progress download disappears the moment it finishes
and gets converted — it doesn't pile up). `--min-free-gb` is a safety
margin on top of that — it stops *new* downloads from starting (letting
whatever's already in flight finish normally) rather than failing
partway through one.

**How "already done" is tracked.** Each title counts as done only once
it's been fully *converted* — a book that only got partway through
downloading is not marked done, so a crash or Ctrl-C mid-download simply
means that title gets attempted again next time. audible-cli itself
handles the actual resume: a large partial download picks up via an HTTP
range request instead of restarting from zero, and a file that's already
fully downloaded just gets skipped outright, not re-fetched. A title
getting killed mid-*conversion* is also safe: the half-written output
lives under a `.tmp` name the whole time and is only renamed to the real
filename on success, so an interrupted run never leaves a broken file
sitting under the real name — and any stale `.tmp` leftovers from a hard
kill get swept up automatically the next time `batch` or `convert` starts.

**Moving files around.** It's safe to move or back up anything already
converted in `./mp3` — resuming only checks ASINs against
`downloads/.sync_progress.json`, never whether the converted file still
physically exists there. It is *not* safe to move a `.aax`/`.aaxc` out of
`./downloads` before it's been converted, though — `batch` finds the file
to convert by looking for it there, and if it's gone, `batch` will just
download that title again.

To start over from scratch, delete `downloads/.sync_progress.json`
(`docker compose run --rm audible-mp3` doesn't need anything special —
that file lives under the mounted `./downloads`, so it survives between
container runs just like the downloaded files do).

## Command reference

| Command | What it does | Options |
|---|---|---|
| `login` | Runs `audible quickstart` (one-time OAuth setup). | — |
| `logout` | Clears your audible-cli login and cached activation bytes, and double-checks with a real audible-cli request that no credentials remain, so `login` can be run fresh. See [below](#logging-out--switching-accounts). | — |
| `activation-bytes` | Prints your account's activation bytes, fetching and caching them on first use. | `--refresh` — ignore the cache and re-fetch from audible-cli. |
| `download` | Downloads purchased `.aax` files into `./downloads`. | `--asin ASIN` — only this title (omit to download everything); `--starts-with LETTER` — only titles starting with this letter (leading "a"/"an"/"the" is ignored, e.g. *The Hobbit* counts as "H"); `--jobs N` — simultaneous downloads (default `3`); `--strict` — abort on the first title that fails instead of the default of skipping it and continuing. |
| `convert` | Converts every `.aax`/`.aaxc` file in `./downloads` to MP3 (or M4B) in `./mp3`, with a progress bar per file. | `--format {mp3,m4b}` — see [Output format: MP3 vs M4B](#output-format-mp3-vs-m4b) (default `mp3`); `--force` — re-convert even if an output file with that name already exists (default: skip). |
| `sync` | Runs `download` then `convert` back to back. | All of `download`'s and `convert`'s options (`--asin`, `--starts-with`, `--jobs`, `--strict`, `--format`, `--force`). |
| `batch` | Keeps up to `--jobs` downloads in flight, converting (and deleting the `.aax` of) each one the instant its own download finishes rather than waiting on the others, so a huge library never needs all its source files on disk at once. Resumable: tracks progress in `downloads/.sync_progress.json`, so stopping and re-running the same command continues where it left off; a failed title is logged and retried next run instead of aborting everything. See [Large libraries / limited disk space](#large-libraries--limited-disk-space). | `--jobs N` — downloads in flight at once (default `3`); `--starts-with LETTER`; `--format {mp3,m4b}` (default `mp3`); `--keep-source` — don't delete the `.aax`/`.aaxc` after converting; `--min-free-gb N` — stop starting new downloads if free space drops below this (default `2.0`, `0` disables). |

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
│   └── .sync_progress.json  # `batch`'s resume state (gitignored)
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
- **A title fails to download.** `download`/`sync`/`batch` all skip a
  failing title and continue with the rest by default — audible-cli still
  prints the underlying error to the terminal, it just isn't fatal to the
  run. Investigate the specific title with `python audible_mp3.py download
  --asin <that ASIN>`; add `--strict` there if you also want this script
  to exit non-zero on that failure instead of continuing past it.
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

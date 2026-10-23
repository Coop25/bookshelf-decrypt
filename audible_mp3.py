#!/usr/bin/env python3
"""Download your own purchased Audible audiobooks and convert them to MP3.

Personal-use tool: everything here operates on YOUR Audible account and
books you've bought, using your own activation bytes. It's a thin wrapper
around audible-cli (for auth/download) and ffmpeg (for decrypt+transcode).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent
DOWNLOAD_DIR = ROOT / "downloads"
MP3_DIR = ROOT / "mp3"
ACTIVATION_BYTES_CACHE = ROOT / ".activation_bytes"


def run(cmd, **kwargs):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)


def cmd_login(_args):
    # Interactive OAuth flow: opens a URL you visit in a browser, then you
    # paste back the redirect URL it lands on. One-time per account.
    run(["audible", "quickstart"])


def audible_config_dir():
    override = os.environ.get("AUDIBLE_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "audible"
    return Path.home() / ".audible"


def cmd_logout(_args):
    # quickstart refuses to run if a config already exists, so clear it
    # (and the activation bytes cache, which is tied to that login) first.
    # Clear the directory's contents rather than removing the directory
    # itself: under Docker it's a bind-mount point, which can't be rmdir'd.
    removed = []
    config_dir = audible_config_dir()
    if config_dir.exists():
        for child in config_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed.append(str(child))
    if ACTIVATION_BYTES_CACHE.exists():
        ACTIVATION_BYTES_CACHE.unlink()
        removed.append(str(ACTIVATION_BYTES_CACHE))

    # Re-check on disk rather than trusting the deletions above succeeded.
    leftovers = list(config_dir.iterdir()) if config_dir.exists() else []
    if ACTIVATION_BYTES_CACHE.exists():
        leftovers.append(ACTIVATION_BYTES_CACHE)
    if leftovers:
        print("Could not confirm removal; still present:")
        for path in leftovers:
            print(f"  {path}")
        sys.exit(1)

    if not removed:
        print("Already logged out; nothing to remove.")
        _verify_logged_out()
        return
    print("Removed:")
    for path in removed:
        print(f"  {path}")
    print(f"Confirmed: no audible-cli credentials remain in {config_dir}.")
    _verify_logged_out()
    print("Run 'login' again to re-authenticate.")


def _verify_logged_out():
    # Belt-and-suspenders: actually ask audible-cli to do something that
    # needs valid credentials, instead of just trusting the file deletions.
    try:
        result = subprocess.run(
            ["audible", "library", "list"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        print("Double-checked: audible-cli has no working credentials.")
    else:
        print(
            "Warning: audible-cli could still list your library. "
            "Credentials may remain somewhere outside the usual config directory."
        )


def get_activation_bytes(force=False):
    if not force and ACTIVATION_BYTES_CACHE.exists():
        return ACTIVATION_BYTES_CACHE.read_text().strip()
    result = subprocess.run(
        ["audible", "activation-bytes"], check=True, capture_output=True, text=True
    )
    bytes_value = result.stdout.strip().splitlines()[-1].strip()
    ACTIVATION_BYTES_CACHE.write_text(bytes_value)
    return bytes_value


def cmd_activation_bytes(args):
    print(get_activation_bytes(force=args.refresh))


def cmd_download(args):
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    cmd = ["audible", "download", "--output-dir", str(DOWNLOAD_DIR), "--aax"]
    if args.asin:
        cmd += ["--asin", args.asin]
    else:
        cmd += ["--all"]
    cmd += ["--jobs", str(args.jobs)]
    if args.ignore_errors:
        cmd.append("--ignore-errors")
    run(cmd)


def probe_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _parse_ffmpeg_time(value):
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def convert_one(src, dest, activation_bytes):
    # ffmpeg's -progress/out_time tracking locks onto the attached cover-art
    # image (a single "frame") instead of the audio when both are mapped to
    # one output, so progress appears stuck at 0% for the whole file. Fix:
    # transcode audio alone first (progress tracks correctly), then do a fast
    # stream-copy remux to reattach the cover art/metadata/chapters.
    duration = probe_duration(src)
    tmp_audio = dest.with_name(dest.name + ".audio.tmp")
    tmp_final = dest.with_name(dest.name + ".tmp")
    try:
        cmd_audio = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-activation_bytes",
            activation_bytes,
            "-i",
            str(src),
            "-map",
            "0:a",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "mp3",
            str(tmp_audio),
        ]
        proc = subprocess.Popen(
            cmd_audio, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        bar = tqdm(total=duration, unit="s", unit_scale=True, disable=duration is None)
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time="):
                    try:
                        elapsed = _parse_ffmpeg_time(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    if duration:
                        bar.n = min(elapsed, duration)
                        bar.refresh()
                elif line and "=" not in line:
                    tqdm.write(line)
            returncode = proc.wait()
        finally:
            bar.close()
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, cmd_audio)

        run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "warning",
                "-i",
                str(tmp_audio),
                "-activation_bytes",
                activation_bytes,
                "-i",
                str(src),
                "-map",
                "0:a",
                "-map",
                "1:v?",
                "-c",
                "copy",
                "-disposition:v:0",
                "attached_pic",
                "-map_metadata",
                "0",
                "-map_chapters",
                "0",
                "-f",
                "mp3",
                str(tmp_final),
            ]
        )
        tmp_final.replace(dest)
    finally:
        tmp_audio.unlink(missing_ok=True)
        tmp_final.unlink(missing_ok=True)


def cmd_convert(args):
    MP3_DIR.mkdir(exist_ok=True)
    activation_bytes = get_activation_bytes()
    aax_files = sorted(DOWNLOAD_DIR.glob("*.aax")) + sorted(DOWNLOAD_DIR.glob("*.aaxc"))
    if not aax_files:
        print(f"No .aax/.aaxc files found in {DOWNLOAD_DIR}")
        return
    total = len(aax_files)
    for i, src in enumerate(aax_files, start=1):
        dest = MP3_DIR / (src.stem + ".mp3")
        if dest.exists() and not args.force:
            print(f"[{i}/{total}] skip (already converted): {dest.name}")
            continue
        print(f"[{i}/{total}] converting: {src.name}")
        convert_one(src, dest, activation_bytes)


def cmd_sync(args):
    cmd_download(args)
    cmd_convert(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Authenticate with Audible (one-time).").set_defaults(
        func=cmd_login
    )

    sub.add_parser(
        "logout",
        help="Clear your audible-cli login and cached activation bytes, so 'login' can run again.",
    ).set_defaults(func=cmd_logout)

    ab = sub.add_parser(
        "activation-bytes", help="Print (and cache) your account's activation bytes."
    )
    ab.add_argument("--refresh", action="store_true", help="Ignore the cache and re-fetch.")
    ab.set_defaults(func=cmd_activation_bytes)

    dl = sub.add_parser("download", help="Download purchased AAX files from your library.")
    dl.add_argument("--asin", help="Only download this one title (by ASIN).")
    dl.add_argument("--jobs", type=int, default=3, help="Simultaneous downloads (default: 3).")
    dl.add_argument(
        "--ignore-errors",
        action="store_true",
        help="Don't abort the whole batch if one title fails; skip it and continue.",
    )
    dl.set_defaults(func=cmd_download)

    cv = sub.add_parser("convert", help="Convert downloaded AAX/AAXC files to MP3.")
    cv.add_argument("--force", action="store_true", help="Re-convert even if an MP3 already exists.")
    cv.set_defaults(func=cmd_convert)

    sy = sub.add_parser("sync", help="Download, then convert, everything (or one --asin).")
    sy.add_argument("--asin")
    sy.add_argument("--force", action="store_true")
    sy.add_argument("--jobs", type=int, default=3, help="Simultaneous downloads (default: 3).")
    sy.add_argument(
        "--ignore-errors",
        action="store_true",
        help="Don't abort the whole batch if one title fails; skip it and continue.",
    )
    sy.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


if __name__ == "__main__":
    main()

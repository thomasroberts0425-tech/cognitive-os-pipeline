#!/usr/bin/env python3
"""
Deepgram transcription for the Canvas study pipeline.

Transcribes lecture audio/video (mp4, mov, m4a, mp3, wav, webm, aac) via the
Deepgram prerecorded API and writes a plain-text transcript — which then flows
through the same path as any reading (_text_cache/ -> /coursenotes).

Cloud transcription is the right call on Intel Macs: Deepgram does the compute,
so a 1-hour lecture transcribes in well under a minute regardless of local CPU.

Key resolution (in order): $DEEPGRAM_API_KEY, then ~/cognitive_os_pipeline/auth/deepgram.key.
The key is never printed or logged.

Usage:
  transcribe.py <media-file> [--model nova-3] [--diarize] [--out <file.txt>]
  transcribe.py <media-file> --cache POL302     # write into that course's _text_cache/
Prints the transcript to stdout if no output target is given.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

AUTH_KEY_FILE = Path.home() / "cognitive_os_pipeline/auth/deepgram.key"
DEFAULT_STAGING_ROOT = Path.home() / "canvas_downloads"
MEDIA_EXTS = {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".webm", ".aac", ".flac", ".mkv", ".mpeg", ".mpga", ".aiff", ".aif"}
# Only these need ffmpeg to extract an audio track; audio formats are sent to
# Deepgram directly (it decodes them natively) — avoids a slow transcode on weak CPUs.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mpeg"}
CONTENT_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
                 ".wav": "audio/wav", ".flac": "audio/flac", ".mpga": "audio/mpeg",
                 ".aiff": "audio/aiff", ".aif": "audio/aiff"}
DG_ENDPOINT = "https://api.deepgram.com/v1/listen"


def load_key():
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if key:
        return key
    if AUTH_KEY_FILE.exists():
        return AUTH_KEY_FILE.read_text().strip()
    return ""


def to_compressed_audio(src: Path) -> Path:
    """Extract a small mono 16kHz mp3 with ffmpeg so uploads stay small even for
    hour-long lectures. Returns a temp .mp3 path the caller must clean up."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found — needed to extract audio from video/large files")
    tmp = Path(tempfile.mkdtemp(prefix="dg_")) / "audio.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", str(tmp)],
        capture_output=True, timeout=1800, check=True,
    )
    return tmp


def deepgram_transcribe(audio: Path, key: str, model: str, diarize: bool, content_type: str) -> dict:
    params = [f"model={model}", "smart_format=true", "punctuate=true", "paragraphs=true"]
    if diarize:
        params.append("diarize=true")
    url = DG_ENDPOINT + "?" + "&".join(params)
    data = audio.read_bytes()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Token {key}")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_transcript(dg_json: dict) -> str:
    try:
        alt = dg_json["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError):
        return ""
    # Prefer paragraph-formatted text (readable), fall back to flat transcript.
    para = alt.get("paragraphs", {})
    if isinstance(para, dict) and para.get("transcript"):
        return para["transcript"].strip()
    return (alt.get("transcript") or "").strip()


def transcribe_file(src: Path, key: str, model="nova-3", diarize=False) -> str:
    """Full pipeline: (extract audio if needed) -> Deepgram -> transcript text."""
    tmp_audio = None
    try:
        ext = src.suffix.lower()
        if ext in VIDEO_EXTS:
            tmp_audio = to_compressed_audio(src)         # extract audio track from video
            audio, ctype = tmp_audio, "audio/mpeg"
        else:
            audio = src                                  # send audio directly — no ffmpeg
            ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        dg = deepgram_transcribe(audio, key, model, diarize, ctype)
        return extract_transcript(dg)
    finally:
        if tmp_audio is not None:
            shutil.rmtree(tmp_audio.parent, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Transcribe lecture audio/video via Deepgram.")
    ap.add_argument("media", help="Path to audio/video file")
    ap.add_argument("--model", default="nova-3")
    ap.add_argument("--diarize", action="store_true", help="Label speakers")
    ap.add_argument("--out", help="Write transcript to this .txt path")
    ap.add_argument("--cache", help="Course code: write into <staging-root>/<course>/_text_cache/")
    ap.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    args = ap.parse_args()

    src = Path(args.media).expanduser()
    if not src.exists():
        print(f"error: file not found: {src}", file=sys.stderr); sys.exit(2)
    if src.suffix.lower() not in MEDIA_EXTS:
        print(f"error: unsupported media type: {src.suffix}", file=sys.stderr); sys.exit(2)

    key = load_key()
    if not key:
        print("error: no Deepgram key (set $DEEPGRAM_API_KEY or ~/cognitive_os_pipeline/auth/deepgram.key)",
              file=sys.stderr); sys.exit(2)

    try:
        text = transcribe_file(src, key, model=args.model, diarize=args.diarize)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        print(f"Deepgram HTTP {e.code}: {body}", file=sys.stderr); sys.exit(1)
    except Exception as e:
        print(f"transcription failed: {e}", file=sys.stderr); sys.exit(1)

    if not text:
        print("error: empty transcript returned", file=sys.stderr); sys.exit(1)

    header = f"# transcript — source: {src.name} (Deepgram {args.model})\n\n"
    if args.cache:
        dest = Path(args.staging_root).expanduser() / args.cache / "_text_cache" / (src.name + ".txt")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + text, encoding="utf-8")
        print(json.dumps({"cached": str(dest), "chars": len(text)}))
    elif args.out:
        Path(args.out).expanduser().write_text(header + text, encoding="utf-8")
        print(json.dumps({"out": args.out, "chars": len(text)}))
    else:
        print(text)


if __name__ == "__main__":
    main()

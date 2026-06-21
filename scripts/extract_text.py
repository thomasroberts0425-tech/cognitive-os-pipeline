#!/usr/bin/env python3
"""
Text extractor for the Canvas study pipeline.

Converts each staged course file into a plain-text cache entry so that:
  - synthesis (/coursenotes) reads fast .txt instead of re-OCR'ing PDFs, and
  - the raw binary can be safely deleted afterward (the .txt is ~10-50x smaller),
    keeping local storage flat across a year of fetching.

Extraction strategy per file type:
  - .pdf  -> pdftotext; if it yields almost nothing (scanned image-only PDF),
            fall back to OCR: pdftoppm renders pages to PNG, tesseract reads them.
  - .doc/.docx/.rtf -> macOS `textutil -convert txt`.
  - other -> skipped (left in place; never deleted).

Idempotent: skips a file whose cache .txt already exists and is newer than the source.
Graceful: on extraction failure it writes NO cache entry and reports status "failed"
so the caller knows NOT to delete that raw file.

Usage:
  extract_text.py --course POL302
  extract_text.py --staging /path/to/staging_dir
  extract_text.py --course POL302 --file "Some Reading.pdf"
Outputs a JSON summary to stdout: {"<filename>": "cached|skipped|failed|unsupported", ...}
Exit code 0 always (per-file status is in the JSON); 2 only on usage error.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_STAGING_ROOT = Path.home() / "canvas_downloads"
CACHE_DIRNAME = "_text_cache"
OFFICE_EXTS = {".doc", ".docx", ".rtf", ".odt"}
# Audio/video -> cloud transcription (Deepgram). Local transcription is impractical
# on Intel Macs, so lectures are transcribed via the API and cached like any reading.
MEDIA_EXTS = {".mp4", ".mov", ".m4a", ".mp3", ".wav", ".webm", ".aac", ".flac",
              ".mkv", ".mpeg", ".mpga", ".aiff", ".aif"}
# Files in the staging dir that are pipeline artifacts, not source materials:
SKIP_NAMES = {"manifest.json", "_announcements.md", "courses.yaml"}
MIN_TEXT_CHARS = 500          # below this, a PDF is treated as scanned -> OCR
OCR_DPI = 150                 # 150 is plenty for tesseract text accuracy and ~2x faster than 200
OCR_MAX_PAGES = 200           # safety cap for runaway OCR jobs
OCR_WORKERS = max(2, (os.cpu_count() or 4) - 1)  # parallel page OCR


def _have(cmd):
    return shutil.which(cmd) is not None


def _clean(text):
    return (text or "").strip()


def _pdftotext(path):
    try:
        out = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            capture_output=True, text=True, timeout=120,
        )
        return out.stdout
    except Exception:
        return ""


def _ocr_pdf(path):
    """Render pages with pdftoppm, OCR each with tesseract. Returns text (may be '')."""
    if not (_have("pdftoppm") and _have("tesseract")):
        return ""
    tmp = Path(tempfile.mkdtemp(prefix="cfetch_ocr_"))
    try:
        prefix = tmp / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(OCR_DPI), str(path), str(prefix)],
            capture_output=True, timeout=600,
        )
        pages = sorted(tmp.glob("page*.png"))[:OCR_MAX_PAGES]

        def _ocr_page(png):
            try:
                # default --psm 3 (auto, no slow orientation detection); -- oem 1 = LSTM
                res = subprocess.run(
                    ["tesseract", str(png), "stdout", "--oem", "1"],
                    capture_output=True, text=True, timeout=120,
                )
                return res.stdout or ""
            except Exception:
                return ""

        with ThreadPoolExecutor(max_workers=OCR_WORKERS) as ex:
            chunks = list(ex.map(_ocr_page, pages))   # preserves page order
        return "\n".join(c for c in chunks if c)
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _textutil(path):
    try:
        out = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return out.stdout
    except Exception:
        return ""


def _transcribe(path):
    """Cloud-transcribe audio/video via the sibling transcribe.py (Deepgram).
    Returns '' on any failure (no key, network, etc.) so the raw file is kept."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import transcribe  # sibling module
        key = transcribe.load_key()
        if not key:
            return ""
        return transcribe.transcribe_file(Path(path), key)
    except Exception:
        return ""


def extract_one(src: Path, cache_dir: Path) -> str:
    """Extract one file to cache_dir/<stem>.txt. Returns a status string."""
    if src.name in SKIP_NAMES or src.name.startswith("."):
        return "unsupported"
    ext = src.suffix.lower()
    dest = cache_dir / (src.name + ".txt")

    # Idempotent: cache newer than source -> skip
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime and dest.stat().st_size > 0:
        return "skipped"

    text = ""
    if ext in MEDIA_EXTS:
        text = _transcribe(src)            # cloud (Deepgram); "" if no key/failure
    elif ext == ".pdf":
        text = _pdftotext(src)
        if len(_clean(text)) < MIN_TEXT_CHARS:
            ocr = _ocr_pdf(src)
            if len(_clean(ocr)) > len(_clean(text)):
                text = ocr
    elif ext in OFFICE_EXTS:
        text = _textutil(src)
    elif ext in {".txt", ".md"}:
        try:
            text = src.read_text(errors="replace")
        except Exception:
            text = ""
    else:
        return "unsupported"

    if len(_clean(text)) < 20:          # nothing usable extracted
        return "failed"

    cache_dir.mkdir(parents=True, exist_ok=True)
    header = f"# extracted text — source: {src.name}\n\n"
    dest.write_text(header + text, encoding="utf-8")
    return "cached"


def prune_raw(staging: Path):
    """Delete raw source files whose text is safely cached. Keeps the cache.
    A raw is removed ONLY if its `_text_cache/<name>.txt` exists and is non-trivial,
    so deletion is always reversible by re-synthesis (and re-fetch if ever needed)."""
    cache_dir = staging / CACHE_DIRNAME
    pruned, kept, freed = [], [], 0
    for p in sorted(staging.iterdir()):
        if not p.is_file() or p.name in SKIP_NAMES or p.name.startswith("."):
            continue
        if p.suffix.lower() in {".txt", ".md", ".json"}:
            continue
        txt = cache_dir / (p.name + ".txt")
        if txt.exists() and txt.stat().st_size > 200:
            freed += p.stat().st_size
            pruned.append(p.name)
            p.unlink()
        else:
            kept.append(p.name)   # not cached -> never delete
    return {"pruned": pruned, "kept_uncached": kept, "freed_bytes": freed}


def main():
    ap = argparse.ArgumentParser(description="Extract staged course files to a text cache.")
    ap.add_argument("--course", help="Course code; staging = <staging-root>/<course>")
    ap.add_argument("--staging", help="Explicit staging directory (overrides --course)")
    ap.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    ap.add_argument("--file", help="Process only this filename within staging")
    ap.add_argument("--prune", action="store_true",
                    help="Delete raw files whose text is already cached (keeps the cache).")
    args = ap.parse_args()

    if args.staging:
        staging = Path(args.staging).expanduser()
    elif args.course:
        staging = Path(args.staging_root).expanduser() / args.course
    else:
        print("error: provide --course or --staging", file=sys.stderr)
        sys.exit(2)

    if not staging.is_dir():
        print(json.dumps({"_error": f"staging not found: {staging}"}))
        sys.exit(0)

    if args.prune:
        print(json.dumps({"staging": str(staging), "prune": prune_raw(staging)}, indent=2))
        return

    cache_dir = staging / CACHE_DIRNAME
    if args.file:
        targets = [staging / args.file]
    else:
        targets = [p for p in sorted(staging.iterdir())
                   if p.is_file() and p.name not in SKIP_NAMES and not p.name.startswith(".")]

    summary = {}
    for t in targets:
        if not t.exists():
            summary[t.name] = "missing"
            continue
        summary[t.name] = extract_one(t, cache_dir)

    counts = {}
    for v in summary.values():
        counts[v] = counts.get(v, 0) + 1
    print(json.dumps({"staging": str(staging), "counts": counts, "files": summary}, indent=2))


if __name__ == "__main__":
    main()

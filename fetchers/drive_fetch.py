#!/usr/bin/env python3
"""
Google Drive fetcher for Cognitive OS pipeline.
Targets recent Otter.ai transcripts (files matching otter*|transcript*|recording*).
Downloads content, writes to vault 01_CAPTURE/Inbox/ as markdown for evening ingestion.
Idempotent: skips if today's cache file already exists.
"""

import json
import os
import sys
import re
import socket
import hashlib
import datetime
from pathlib import Path

socket.setdefaulttimeout(60)  # 60-second timeout on all network operations

SCRIPT_DIR = Path(__file__).parent.parent
AUTH_DIR = SCRIPT_DIR / "auth"
CACHE_DIR = SCRIPT_DIR / "cache"
RUN_LOG = CACHE_DIR / "run_log.jsonl"
VAULT_INBOX = Path.home() / "Documents/NEW_COGNITIVE_OS/01_CAPTURE/Inbox"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

TRANSCRIPT_PATTERN = re.compile(r"(otter|transcript|recording)", re.IGNORECASE)


def get_credentials():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    token_path = AUTH_DIR / "token.json"
    creds_path = AUTH_DIR / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                print(f"ERROR: credentials.json not found at {creds_path}", file=sys.stderr)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def sanitize_filename(name):
    return re.sub(r"[^\w\s\-]", "", name).strip().replace(" ", "_")


def main():
    today = datetime.date.today().isoformat()
    cache_file = CACHE_DIR / f"drive_{today}.json"

    if cache_file.exists():
        _log(today, "drive_fetch", "skipped", None)
        print(f"Cache exists: {cache_file} — skipping fetch")
        return

    try:
        from googleapiclient.discovery import build
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds)

        # Look for recent transcript-style files (last 48h to catch overnight)
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=48)).isoformat() + "Z"
        results = service.files().list(
            q=f"modifiedTime > '{cutoff}' and trashed = false",
            fields="files(id, name, mimeType, modifiedTime, size)",
            orderBy="modifiedTime desc",
            pageSize=50
        ).execute()

        files = results.get("files", [])
        transcript_files = [f for f in files if TRANSCRIPT_PATTERN.search(f.get("name", ""))]

        dropped = []
        VAULT_INBOX.mkdir(parents=True, exist_ok=True)

        for f in transcript_files:
            file_id = f["id"]
            name = f["name"]
            safe_name = sanitize_filename(name)
            out_path = VAULT_INBOX / f"{today}_Otter_{safe_name}.md"

            if out_path.exists():
                print(f"  Already in inbox: {out_path.name}")
                continue

            # Only ingest actual TEXT transcripts. The name filter matches
            # "recording", which also catches raw screen-recording VIDEO files
            # (ScreenRecording*.mov/.mp4). Never decode binary as text — that
            # previously wrote 300MB+ of garbage video bytes into .md files.
            mime = f.get("mimeType", "")
            size = int(f.get("size", 0) or 0)
            TEXT_MIMES = ("text/", "application/vnd.google-apps.document")
            MAX_TEXT_BYTES = 5_000_000  # transcripts are small; bigger = not text

            is_gdoc = "google-apps.document" in mime
            is_text = mime.startswith("text/")
            if not (is_gdoc or is_text):
                print(f"  SKIP (non-text mime {mime!r}): {name}", file=sys.stderr)
                continue
            if not is_gdoc and size and size > MAX_TEXT_BYTES:
                print(f"  SKIP (text file too large, {size/1e6:.0f}MB — suspect binary): {name}", file=sys.stderr)
                continue

            try:
                if is_gdoc:
                    content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
                else:
                    content = service.files().get_media(fileId=file_id).execute()
                text = content.decode("utf-8") if isinstance(content, bytes) else content

                # Guard: if the decoded text is mostly non-printable, it isn't a
                # transcript — refuse to write it.
                sample = text[:4000]
                if sample:
                    printable = sum(1 for c in sample if c.isprintable() or c in "\n\r\t")
                    if printable / len(sample) < 0.85:
                        print(f"  SKIP (decoded content not text-like): {name}", file=sys.stderr)
                        continue

                md_content = f"---\nsource: google-drive\noriginal_name: {name}\nfetched: {today}\ntags: [capture/otter, capture/transcript]\n---\n\n# {name}\n\n{text}\n"
                out_path.write_text(md_content)
                dropped.append(str(out_path.name))
                print(f"  Dropped: {out_path.name}")
            except UnicodeDecodeError:
                print(f"  SKIP (not valid UTF-8 text): {name}", file=sys.stderr)
            except Exception as dl_err:
                print(f"  WARN: could not download {name}: {dl_err}", file=sys.stderr)

        output = {"date": today, "transcripts_found": len(transcript_files), "dropped_to_inbox": dropped}
        cache_file.write_text(json.dumps(output, indent=2))
        sha = hashlib.sha256(cache_file.read_bytes()).hexdigest()[:12]
        _log(today, "drive_fetch", "success", sha)
        print(f"Drive fetch done: {len(transcript_files)} transcripts found, {len(dropped)} dropped to inbox")

    except Exception as e:
        _log(today, "drive_fetch", f"error: {e}", None)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _log(date, job, status, sha):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"date": date, "job": job, "status": status, "sha256": sha}
    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()

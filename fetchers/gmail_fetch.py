#!/usr/bin/env python3
"""
Gmail fetcher for Cognitive OS pipeline.
Fetches today's unread email (excluding promotions/social/updates/forums),
triages into action_required / fyi_only / skip, writes cache/gmail_YYYY-MM-DD.json.
Idempotent: skips if today's cache file already exists.
"""

import json
import os
import sys
import socket
import hashlib
import datetime
from pathlib import Path

socket.setdefaulttimeout(60)  # 60-second timeout on all network operations

SCRIPT_DIR = Path(__file__).parent.parent
AUTH_DIR = SCRIPT_DIR / "auth"
CACHE_DIR = SCRIPT_DIR / "cache"
RUN_LOG = CACHE_DIR / "run_log.jsonl"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
QUERY = "is:unread newer_than:1d -category:promotions -category:social -category:updates -category:forums"


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
                print("Run SETUP.md OAuth bootstrap first.", file=sys.stderr)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def triage_thread(service, thread_id):
    """Fetch a thread and classify it as action_required, fyi_only, or skip."""
    thread = service.users().threads().get(userId="me", id=thread_id, format="metadata",
        metadataHeaders=["Subject", "From", "Date"]).execute()

    messages = thread.get("messages", [])
    if not messages:
        return None, None

    headers = {h["name"]: h["value"] for h in messages[0].get("payload", {}).get("headers", [])}
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "")
    date = headers.get("Date", "")

    snippet = messages[-1].get("snippet", "")

    ACTION_KEYWORDS = ["reply", "urgent", "action required", "please", "?", "respond", "confirm",
                       "need", "asap", "deadline", "invoice", "contract", "approval"]
    is_action = any(kw in (subject + snippet).lower() for kw in ACTION_KEYWORDS)

    entry = {
        "thread_id": thread_id,
        "subject": subject,
        "from": sender,
        "date": date,
        "snippet": snippet,
        "message_count": len(messages)
    }

    category = "action_required" if is_action else "fyi_only"
    return category, entry


def main():
    today = datetime.date.today().isoformat()
    cache_file = CACHE_DIR / f"gmail_{today}.json"

    if cache_file.exists():
        _log(today, "gmail_fetch", "skipped", None)
        print(f"Cache exists: {cache_file} — skipping fetch")
        return

    try:
        from googleapiclient.discovery import build
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds)

        results = service.users().threads().list(
            userId="me", q=QUERY, maxResults=50
        ).execute()

        threads = results.get("threads", [])
        output = {"date": today, "action_required": [], "fyi_only": [], "skip": [], "query": QUERY}

        for t in threads:
            category, entry = triage_thread(service, t["id"])
            if entry:
                output[category].append(entry)

        cache_file.write_text(json.dumps(output, indent=2))
        sha = hashlib.sha256(cache_file.read_bytes()).hexdigest()[:12]
        _log(today, "gmail_fetch", "success", sha)
        print(f"Fetched {len(output['action_required'])} action, {len(output['fyi_only'])} fyi — {cache_file}")

    except Exception as e:
        _log(today, "gmail_fetch", f"error: {e}", None)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _log(date, job, status, sha):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"date": date, "job": job, "status": status, "sha256": sha}
    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()

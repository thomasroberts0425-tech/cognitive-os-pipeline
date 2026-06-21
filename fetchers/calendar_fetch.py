#!/usr/bin/env python3
"""
Google Calendar fetcher for Cognitive OS pipeline.
Fetches today's events as fixed anchors for time-blocking.
Writes cache/calendar_YYYY-MM-DD.json. Idempotent.
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


def main():
    today = datetime.date.today().isoformat()
    cache_file = CACHE_DIR / f"calendar_{today}.json"

    if cache_file.exists():
        _log(today, "calendar_fetch", "skipped", None)
        print(f"Cache exists: {cache_file} — skipping fetch")
        return

    try:
        from googleapiclient.discovery import build
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)

        start = datetime.datetime.combine(datetime.date.today(), datetime.time.min).isoformat() + "Z"
        end = datetime.datetime.combine(datetime.date.today(), datetime.time.max).isoformat() + "Z"

        events_result = service.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        raw_events = events_result.get("items", [])
        events = []
        for e in raw_events:
            start_val = e.get("start", {})
            end_val = e.get("end", {})
            events.append({
                "id": e.get("id"),
                "summary": e.get("summary", "(no title)"),
                "start": start_val.get("dateTime") or start_val.get("date"),
                "end": end_val.get("dateTime") or end_val.get("date"),
                "location": e.get("location", ""),
                "description": e.get("description", ""),
                "all_day": "date" in start_val and "dateTime" not in start_val,
                "fixed_anchor": True
            })

        output = {"date": today, "events": events, "count": len(events)}
        cache_file.write_text(json.dumps(output, indent=2))
        sha = hashlib.sha256(cache_file.read_bytes()).hexdigest()[:12]
        _log(today, "calendar_fetch", "success", sha)
        print(f"Fetched {len(events)} events — {cache_file}")

    except Exception as e:
        _log(today, "calendar_fetch", f"error: {e}", None)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _log(date, job, status, sha):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"date": date, "job": job, "status": status, "sha256": sha}
    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main()

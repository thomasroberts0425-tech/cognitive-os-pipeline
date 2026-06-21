#!/usr/bin/env python3
"""
Cognitive OS — OAuth token health checker.
Validates all 3 Google API tokens (Gmail, Calendar, Drive) with a minimal test call.
Writes results to ~/cognitive_os_pipeline/cache/health.json under key "oauth".
Usage:
  python3 check_auth.py              # check all
  python3 check_auth.py --reauth gmail   # re-run OAuth flow for one service
"""

import json
import os
import sys
import subprocess
import datetime
from pathlib import Path

PIPELINE = Path.home() / "cognitive_os_pipeline"
AUTH_DIR = PIPELINE / "auth"
CACHE_DIR = PIPELINE / "cache"
HEALTH_FILE = CACHE_DIR / "health.json"
NOTIFY = PIPELINE / "scripts" / "notify.sh"

SCOPES = {
    "gmail":    "https://www.googleapis.com/auth/gmail.readonly",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "drive":    "https://www.googleapis.com/auth/drive.readonly",
}

def notify(title: str, body: str):
    if NOTIFY.exists():
        subprocess.run([str(NOTIFY), title, body], check=False)

def load_health() -> dict:
    try:
        return json.loads(HEALTH_FILE.read_text())
    except Exception:
        return {}

def save_health(data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(data, indent=2))

def get_credentials():
    """Load OAuth credentials from token.json."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        token_path = AUTH_DIR / "token.json"
        creds = Credentials.from_authorized_user_file(str(token_path), list(SCOPES.values()))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        return creds
    except Exception as e:
        return None, str(e)

def check_gmail(creds) -> tuple[bool, str]:
    try:
        from googleapiclient.discovery import build
        import socket
        socket.setdefaulttimeout(30)
        service = build("gmail", "v1", credentials=creds)
        service.users().getProfile(userId="me").execute()
        return True, "ok"
    except Exception as e:
        return False, str(e)

def check_calendar(creds) -> tuple[bool, str]:
    try:
        from googleapiclient.discovery import build
        import socket
        socket.setdefaulttimeout(30)
        service = build("calendar", "v3", credentials=creds)
        service.calendarList().list(maxResults=1).execute()
        return True, "ok"
    except Exception as e:
        return False, str(e)

def check_drive(creds) -> tuple[bool, str]:
    try:
        from googleapiclient.discovery import build
        import socket
        socket.setdefaulttimeout(30)
        service = build("drive", "v3", credentials=creds)
        service.files().list(pageSize=1).execute()
        return True, "ok"
    except Exception as e:
        return False, str(e)

def reauth(service: str):
    """Re-run the OAuth flow for a specific service."""
    print(f"Re-authenticating {service}...")
    # The existing auth flow is in the fetchers — run gmail_fetch.py which triggers re-auth
    fetcher_map = {
        "gmail": PIPELINE / "fetchers" / "gmail_fetch.py",
        "calendar": PIPELINE / "fetchers" / "calendar_fetch.py",
        "drive": PIPELINE / "fetchers" / "drive_fetch.py",
    }
    fetcher = fetcher_map.get(service)
    if not fetcher or not fetcher.exists():
        print(f"  ERROR: no fetcher found for {service}")
        sys.exit(1)
    # Remove today's cache so fetcher runs fresh
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")
    cache_file = CACHE_DIR / f"{service}_{today}.json"
    cache_file.unlink(missing_ok=True)
    # Delete token.json to force re-auth
    token_path = AUTH_DIR / "token.json"
    if token_path.exists():
        token_path.unlink()
        print(f"  Removed stale token — a browser window will open for re-auth")
    result = subprocess.run([sys.executable, str(fetcher)], check=False)
    sys.exit(result.returncode)

def main():
    if "--reauth" in sys.argv:
        idx = sys.argv.index("--reauth")
        service = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if service not in SCOPES:
            print(f"Usage: check_auth.py --reauth [gmail|calendar|drive]")
            sys.exit(1)
        reauth(service)
        return

    print("Cognitive OS — OAuth token health check")
    print(f"  Token path: {AUTH_DIR / 'token.json'}")

    creds = get_credentials()
    if creds is None or (isinstance(creds, tuple) and not creds[0]):
        msg = "Cannot load credentials from token.json — run: python3 check_auth.py --reauth gmail"
        print(f"  ERROR: {msg}")
        notify("Cognitive OS: Auth failure", msg)
        health = load_health()
        health["oauth"] = {"gmail": "no_token", "calendar": "no_token", "drive": "no_token"}
        save_health(health)
        sys.exit(1)

    checks = {
        "gmail":    check_gmail(creds),
        "calendar": check_calendar(creds),
        "drive":    check_drive(creds),
    }

    health = load_health()
    oauth_status = {}
    all_ok = True

    for service, (ok, detail) in checks.items():
        status = "ok" if ok else "expired"
        oauth_status[service] = status
        icon = "✅" if ok else "❌"
        print(f"  {icon} {service}: {detail}")
        if not ok:
            all_ok = False
            notify(
                f"Cognitive OS: {service} auth expired",
                f"Run: python3 ~/cognitive_os_pipeline/scripts/check_auth.py --reauth {service}"
            )

    health["oauth"] = oauth_status
    health["oauth"]["checked"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    save_health(health)

    if all_ok:
        print("\n  All tokens healthy.")
    else:
        print("\n  One or more tokens need re-auth. Check notifications.")
        sys.exit(1)

if __name__ == "__main__":
    main()

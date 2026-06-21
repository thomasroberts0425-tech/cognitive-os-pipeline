#!/bin/zsh
# Cognitive OS — Evening Routine v2.2
# Flow: Gmail refresh → Drive fetch → validator → Claude synthesis → routing log
# Invoked by launchd: com.cognitiveos.evening

export PATH="/Users/thomasroberts/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="/Users/thomasroberts"
export USER="thomasroberts"

ROUTINE_NAME="evening_routine"
source "$(dirname "$0")/common.sh"

acquire_lock

DATE=$(date +%Y-%m-%d)
NOTE_PATH="$VAULT/02_OPERATIONS/Daily_Notes/$DATE.md"
GMAIL_CACHE="$CACHE_DIR/gmail_$DATE.json"
ROUTING_LOG="$CACHE_DIR/routing_log.jsonl"

_log_raw "start — date=$DATE"

# ── Require critical binaries ──────────────────────────────────────────────
require "$PYTHON"
require /Users/thomasroberts/.local/bin/claude

# ── 1. Gmail refresh (only if morning cache is >6h old) ───────────────────
if [ -f "$GMAIL_CACHE" ]; then
  CACHE_AGE=$(( $(date +%s) - $(stat -f %m "$GMAIL_CACHE") ))
  if [ "$CACHE_AGE" -gt 21600 ]; then
    rm "$GMAIL_CACHE"
    _log_raw "gmail_cache_refresh: morning cache >6h old, re-fetching"
  fi
fi
with_timeout 120 "$PYTHON" "$PIPELINE/fetchers/gmail_fetch.py" || {
  _log_raw "warn: gmail_fetch failed — evening will use state files only"
}

# ── 2. Drive fetch (Otter transcripts) ────────────────────────────────────
with_timeout 120 "$PYTHON" "$PIPELINE/fetchers/drive_fetch.py" || {
  _log_raw "warn: drive_fetch failed — no new transcripts this run"
}

# ── 2b. Lesson fetch (AI Engineering course captures) ─────────────────────
# PAUSED 2026-06-10 until AI Engineering Phase 00 is actually started.
# The fetcher was pulling lessons ahead of the course, piling up 67 orphan
# stubs in the Inbox. Re-enable (uncomment) when AI-Engineering-Hub.md status
# advances past "Not started".
# with_timeout 60 "$PYTHON" "$PIPELINE/fetchers/lesson_fetcher.py" || {
#   _log_raw "warn: lesson_fetch failed — no lesson captures this run"
# }

# ── 3. Run vault validator ────────────────────────────────────────────────
with_timeout 60 "$PYTHON" "$SCRIPTS_DIR/validate_vault.py" || {
  _log_raw "warn: validate_vault failed — Validation_Report.md may be stale"
}

# ── 4. Idempotency check ──────────────────────────────────────────────────
if grep -q "<!-- EVENING_END -->" "$NOTE_PATH" 2>/dev/null; then
  EVENING_CONTENT=$(awk '/<!-- EVENING_START -->/{f=1;next} /<!-- EVENING_END -->/{f=0} f' "$NOTE_PATH" | tr -d '[:space:]')
  if [ ${#EVENING_CONTENT} -gt 50 ]; then
    _log_raw "skipped: evening synthesis already present (${#EVENING_CONTENT} chars)"
    log_status "$ROUTINE_NAME" "skipped"
    exit 0
  fi
fi

# ── 5. Build inbox file list ──────────────────────────────────────────────
INBOX_FILES=$(ls "$VAULT/01_CAPTURE/Inbox/"*.md 2>/dev/null | head -20 | tr '\n' ' ')

# ── 6. Invoke Claude ──────────────────────────────────────────────────────
_log_raw "claude_start"
with_timeout 300 /Users/thomasroberts/.local/bin/claude \
  --dangerously-skip-permissions \
  -p "Read $VAULT/00_SYSTEM/Prompts/Evening_Synthesis_Prompt.md in full. Then read:
- Gmail cache: $CACHE_DIR/gmail_$DATE.json
- Today's daily note: $NOTE_PATH
- All 5 state files in $VAULT/04_AI_CONTEXT/Project_States/
- Inbox files to process: $INBOX_FILES
- Entity registry: $VAULT/00_SYSTEM/Entity_Registry.md
Perform inbox digestion and evening synthesis. Write results into $NOTE_PATH between <!-- EVENING_START --> and <!-- EVENING_END --> markers. Log all routing decisions to $ROUTING_LOG." \
|| fail_loud "claude invocation failed"

# ── 7. Verify and log ─────────────────────────────────────────────────────
[ -f "$NOTE_PATH" ] || fail_loud "note file missing after Claude invocation"

SHA=$(shasum -a 256 "$NOTE_PATH" | cut -c1-12)
_log_raw "complete ($SHA)"
log_status "$ROUTINE_NAME" "success"

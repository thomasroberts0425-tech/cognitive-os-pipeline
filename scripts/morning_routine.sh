#!/bin/zsh
# Cognitive OS — Morning Routine v2.2
# Flow: auth check → Python fetchers → verify cache → update Daily_Log → Claude briefing
# Invoked by launchd: com.cognitiveos.morning

export PATH="/Users/thomasroberts/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="/Users/thomasroberts"
export USER="thomasroberts"

ROUTINE_NAME="morning_routine"
source "$(dirname "$0")/common.sh"

acquire_lock

DATE=$(date +%Y-%m-%d)
NOTE_PATH="$VAULT/02_OPERATIONS/Daily_Notes/$DATE.md"
TEMPLATE_PATH="$VAULT/00_SYSTEM/Templates/Daily_Note_Template.md"
GMAIL_CACHE="$CACHE_DIR/gmail_$DATE.json"
CALENDAR_CACHE="$CACHE_DIR/calendar_$DATE.json"

_log_raw "start — date=$DATE"

# ── Require critical binaries ──────────────────────────────────────────────
require "$PYTHON"
require /Users/thomasroberts/.local/bin/claude

# ── 1. Auth health check ──────────────────────────────────────────────────
_log_raw "auth_check"
with_timeout 60 "$PYTHON" "$SCRIPTS_DIR/check_auth.py" || {
  _log_raw "warn: auth check failed — continuing (fetchers will handle gracefully)"
}

# ── 2. Run Python fetchers ────────────────────────────────────────────────
_log_raw "fetchers_start"
with_timeout 120 "$PYTHON" "$PIPELINE/fetchers/gmail_fetch.py" || fail_loud "gmail_fetch failed — cache unavailable"
with_timeout 120 "$PYTHON" "$PIPELINE/fetchers/calendar_fetch.py" || fail_loud "calendar_fetch failed — cache unavailable"

# ── 3. Verify cache files exist ───────────────────────────────────────────
[ -f "$GMAIL_CACHE" ]    || fail_loud "gmail cache missing after fetch"
[ -f "$CALENDAR_CACHE" ] || fail_loud "calendar cache missing after fetch"

# ── 4. Update Daily_Log index ─────────────────────────────────────────────
with_timeout 60 "$PYTHON" "$SCRIPTS_DIR/update_daily_log.py" || {
  _log_raw "warn: update_daily_log failed — non-fatal, continuing"
}

# ── 5. Create daily note from template if needed ──────────────────────────
mkdir -p "$VAULT/02_OPERATIONS/Daily_Notes"
if [ ! -f "$NOTE_PATH" ]; then
  [ -f "$TEMPLATE_PATH" ] || fail_loud "Daily_Note_Template.md not found at $TEMPLATE_PATH"
  cp "$TEMPLATE_PATH" "$NOTE_PATH"
  sed -i '' "s/{{date:YYYY-MM-DD}}/$DATE/g" "$NOTE_PATH"
  _log_raw "note_created: $NOTE_PATH"
fi

# ── 6. Idempotency check ──────────────────────────────────────────────────
if grep -q "<!-- BRIEFING_END -->" "$NOTE_PATH" 2>/dev/null; then
  BRIEFING_CONTENT=$(awk '/<!-- BRIEFING_START -->/{f=1;next} /<!-- BRIEFING_END -->/{f=0} f' "$NOTE_PATH" | tr -d '[:space:]')
  if [ ${#BRIEFING_CONTENT} -gt 50 ]; then
    _log_raw "skipped: briefing already present (${#BRIEFING_CONTENT} chars)"
    log_status "$ROUTINE_NAME" "skipped"
    exit 0
  fi
fi

# ── 7. Invoke Claude ──────────────────────────────────────────────────────
_log_raw "claude_start"
with_timeout 300 /Users/thomasroberts/.local/bin/claude \
  --dangerously-skip-permissions \
  -p "Read $VAULT/00_SYSTEM/Prompts/Morning_Synthesis_Prompt.md in full. Then read:
- Gmail cache: $GMAIL_CACHE
- Calendar cache: $CALENDAR_CACHE
- Validation report: $VAULT/00_SYSTEM/Validation_Report.md
- Health status: $CACHE_DIR/health.json
- All 5 state files in $VAULT/04_AI_CONTEXT/Project_States/
- Yesterday's daily note (if it exists in $VAULT/02_OPERATIONS/Daily_Notes/)
- Entity registry: $VAULT/00_SYSTEM/Entity_Registry.md
Generate today's morning briefing and write it into $NOTE_PATH between <!-- BRIEFING_START --> and <!-- BRIEFING_END --> markers." \
|| fail_loud "claude invocation failed"

# ── 8. Verify and log ─────────────────────────────────────────────────────
[ -f "$NOTE_PATH" ] || fail_loud "note file missing after Claude invocation"

SHA=$(shasum -a 256 "$NOTE_PATH" | cut -c1-12)
_log_raw "complete: briefing written ($SHA)"

# ── Update CLAUDE.md date ──────────────────────────────────────────────────
sed -i '' "s/Today's date is .*/Today's date is $(date +%Y-%m-%d)./" ~/.claude/CLAUDE.md
_log_raw "claude_md_date_updated: $(date +%Y-%m-%d)"

log_status "$ROUTINE_NAME" "success"

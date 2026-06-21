#!/bin/zsh
# Cognitive OS — Weekly Routine v2.2
# Flow: create review file → Claude weekly synthesis → update Daily_Log index
# Invoked by launchd: com.cognitiveos.weekly

export PATH="/Users/thomasroberts/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="/Users/thomasroberts"
export USER="thomasroberts"

ROUTINE_NAME="weekly_routine"
source "$(dirname "$0")/common.sh"

acquire_lock

DATE=$(date +%Y-%m-%d)
WEEK=$(date +%Y-W%V)
REVIEW_PATH="$VAULT/02_OPERATIONS/Reviews/$WEEK.md"

_log_raw "start — date=$DATE week=$WEEK"

# ── Require critical binaries ──────────────────────────────────────────────
require "$PYTHON"
require /Users/thomasroberts/.local/bin/claude

# ── Idempotency — skip if this week's review already exists ───────────────
if [ -f "$REVIEW_PATH" ]; then
  _log_raw "skipped: $REVIEW_PATH already exists"
  log_status "$ROUTINE_NAME" "skipped"
  exit 0
fi

mkdir -p "$VAULT/02_OPERATIONS/Reviews"

# ── Invoke Claude ─────────────────────────────────────────────────────────
_log_raw "claude_start"
with_timeout 300 /Users/thomasroberts/.local/bin/claude \
  --dangerously-skip-permissions \
  -p "Read $VAULT/00_SYSTEM/Prompts/Weekly_Review_Prompt.md in full. Then read:
- All daily notes from the past 7 days in $VAULT/02_OPERATIONS/Daily_Notes/
- All 5 state files in $VAULT/04_AI_CONTEXT/Project_States/
- Validation report: $VAULT/00_SYSTEM/Validation_Report.md
- Longitudinal Ledger (last 200 lines): $VAULT/04_AI_CONTEXT/Longitudinal_Intelligence/Longitudinal_Ledger.md
Generate the Strategic Diagnostic Report for week $WEEK and write it as a new file at $REVIEW_PATH." \
|| fail_loud "claude invocation failed"

# ── Verify review was created ─────────────────────────────────────────────
[ -f "$REVIEW_PATH" ] || fail_loud "review file not created after Claude invocation"

SHA=$(shasum -a 256 "$REVIEW_PATH" | cut -c1-12)
_log_raw "complete: $REVIEW_PATH ($SHA)"

# ── Update Daily_Log index ────────────────────────────────────────────────
with_timeout 60 "$PYTHON" "$SCRIPTS_DIR/update_daily_log.py" || {
  _log_raw "warn: update_daily_log failed — non-fatal"
}

log_status "$ROUTINE_NAME" "success"

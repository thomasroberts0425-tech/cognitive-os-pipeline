#!/bin/zsh
# Cognitive OS — Shared functions for all routines
# Source this at the top of every routine: source "$(dirname "$0")/common.sh"

# ── Paths ────────────────────────────────────────────────────────────────────
PIPELINE="/Users/thomasroberts/cognitive_os_pipeline"
VAULT="/Users/thomasroberts/Documents/NEW_COGNITIVE_OS"
CACHE_DIR="$PIPELINE/cache"
SCRIPTS_DIR="$PIPELINE/scripts"
HEALTH_FILE="$CACHE_DIR/health.json"
RUN_LOG="$CACHE_DIR/run_log.jsonl"

# ── Python ───────────────────────────────────────────────────────────────────
PYTHON="${PYTHON_BIN:-/usr/bin/python3}"

# ── Logging ──────────────────────────────────────────────────────────────────
_log_raw() {
  local job="${ROUTINE_NAME:-unknown}"
  echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"job\":\"$job\",\"msg\":\"$1\"}" >> "$RUN_LOG"
  echo "[$job] $1" >&2
}

log_status() {
  # log_status <component> <status> [optional_detail]
  # Note: avoid 'status' as local var name — it's a zsh read-only built-in
  local component="$1"
  local log_status_val="$2"
  local detail="${3:-}"
  local job="${ROUTINE_NAME:-unknown}"
  _log_raw "${component}: ${log_status_val}${detail:+ — $detail}"

  # Update health.json for the top-level routine status
  if [ "$component" = "$job" ]; then
    python3 - <<PYEOF 2>/dev/null
import json, os, datetime
f = "$HEALTH_FILE"
data = {}
try:
    with open(f) as fh:
        data = json.load(fh)
except Exception:
    pass
data["$job"] = {
    "last_run": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
    "status": "$log_status_val",
}
if "$detail":
    data["$job"]["detail"] = "$detail"
os.makedirs(os.path.dirname(f), exist_ok=True)
with open(f, "w") as fh:
    json.dump(data, fh, indent=2)
PYEOF
  fi
}

# ── Fail loud ────────────────────────────────────────────────────────────────
fail_loud() {
  local msg="$1"
  local job="${ROUTINE_NAME:-routine}"
  _log_raw "FATAL: $msg"
  log_status "$job" "failed" "$msg"
  # Fire macOS notification
  if [ -x "$SCRIPTS_DIR/notify.sh" ]; then
    "$SCRIPTS_DIR/notify.sh" "Cognitive OS: $job failed" "$msg"
  fi
  # Release lock before exit
  _release_lock
  exit 1
}

# ── Require binary ───────────────────────────────────────────────────────────
require() {
  local bin="$1"
  if ! command -v "$bin" &>/dev/null && [ ! -x "$bin" ]; then
    fail_loud "Required binary not found: $bin"
  fi
}

# ── Timeout wrapper ──────────────────────────────────────────────────────────
with_timeout() {
  local secs="$1"
  shift
  # gtimeout (GNU coreutils) if available, else plain execution
  if command -v gtimeout &>/dev/null; then
    gtimeout "$secs" "$@"
  else
    # macOS: use perl-based timeout fallback
    perl -e "alarm $secs; exec @ARGV" "$@"
  fi
}

# ── Lock file ────────────────────────────────────────────────────────────────
_LOCK_FILE=""

acquire_lock() {
  local name="${ROUTINE_NAME:-unknown}"
  _LOCK_FILE="$CACHE_DIR/${name}.lock"
  if [ -f "$_LOCK_FILE" ]; then
    local pid
    pid=$(cat "$_LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
      _log_raw "Already running (pid $pid) — exiting"
      exit 0
    else
      _log_raw "Stale lock found (pid $pid) — removing"
      rm -f "$_LOCK_FILE"
    fi
  fi
  echo $$ > "$_LOCK_FILE"
}

_release_lock() {
  [ -n "$_LOCK_FILE" ] && rm -f "$_LOCK_FILE"
}

# Auto-release on normal exit
trap '_release_lock' EXIT INT TERM

#!/bin/zsh
# Cognitive OS — Canvas synthesis background routine
#
# ARCHITECTURE (rebuilt 2026-09-01 for the second-brain vault):
#   The Canvas FETCH step needs the Claude in Chrome MCP, an app<->extension bridge
#   NOT available to a headless `claude -p`. So this job does NOT fetch. You run
#   /canvas-fetch in the app; this runs the headless-safe remainder:
#
#     extract -> synthesize -> prune
#
#   1. extract_text.py   raw .pdf/.mp3/.docx -> _text_cache/<name>.txt
#                        (PDFs via pdftotext+OCR, media via Deepgram)
#   2. /coursenotes      _text_cache/*.txt -> notes in Areas/School/<COURSE>/Work/
#   3. --prune           deletes raw files whose .txt is cached
#
#   Step 3 is what clears the trigger. A course has "pending synthesis" whenever raw
#   (un-pruned) files remain in its staging dir. This is STATE-based, not date-based:
#   a missed run costs latency, never data — the files are still pending tomorrow.
#
#   /coursenotes deliberately never prunes (see its SKILL.md "Never" section), which is
#   why pruning is an explicit step here rather than delegated. The old chain hid it
#   inside /canvas-sync, a skill that no longer exists.
#
# Enable:  launchctl load ~/Library/LaunchAgents/com.cognitiveos.canvassync.plist
# Disable: launchctl unload ~/Library/LaunchAgents/com.cognitiveos.canvassync.plist

set -uo pipefail
ROUTINE_NAME="canvas_sync"

# launchd provides a minimal PATH. pdftotext/pdftoppm live in /opt/homebrew/bin and
# extract_text.py shells out to them; without this they silently fail and every PDF
# is reported "failed". Same omission broke run-routine.sh in August. Do not remove.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"

source "$(dirname "$0")/common.sh" 2>/dev/null || true

STAGING_ROOT="$HOME/canvas_downloads"
PIPELINE_DIR="$HOME/cognitive_os_pipeline"
EXTRACT="$PIPELINE_DIR/scripts/extract_text.py"
NOTIFY="$PIPELINE_DIR/scripts/notify.sh"
SYNTH_LOG="$PIPELINE_DIR/cache/canvas_sync.log"
STATE_DIR="$PIPELINE_DIR/cache/canvas_sync_state"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
PY="${PYTHON:-/usr/bin/python3}"

# common.sh sets VAULT to the OLD vault (NEW_COGNITIVE_OS). /coursenotes writes to the
# new one and reads project-level CLAUDE.md, so claude -p must run with this cwd.
SB_VAULT="$HOME/Documents/second-brain"

SYNTH_TIMEOUT=1500      # 25 min — synthesis fans out a subagent per module
COOLDOWN_SECS=10800     # 3h before retrying a course that failed
MAX_FAILURES=5          # after this many, stop retrying and notify once
QUIET_START=1410        # 23:30 — nightly (23:45) and daily (00:01-06:00) own the night
QUIET_END=390           # 06:30    two `claude -p` jobs at once risks the usage cap

# ── Preconditions ────────────────────────────────────────────────────────────
[[ -x "$CLAUDE_BIN" ]] || { echo "[canvas_sync] claude binary not found at $CLAUDE_BIN" >&2; exit 0; }
[[ -d "$STAGING_ROOT" ]] || { echo "[canvas_sync] no staging root" >&2; exit 0; }
[[ -f "$EXTRACT" ]] || { echo "[canvas_sync] extract_text.py missing" >&2; exit 0; }
[[ -d "$SB_VAULT" ]] || { echo "[canvas_sync] vault not found at $SB_VAULT" >&2; exit 0; }
mkdir -p "$STATE_DIR" "$(dirname "$SYNTH_LOG")"

# ── Quiet hours ──────────────────────────────────────────────────────────────
# Computed with date(1) at run time, NOT StartCalendarInterval — that is evaluated by
# UserEventAgent, which caches the timezone at load and fires hours off after a move.
# See the comment in com.thomasroberts.secondbrain.nightly.plist. Do not "simplify".
NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
if [[ "$NOW_MIN" -ge "$QUIET_START" || "$NOW_MIN" -lt "$QUIET_END" ]]; then
  exit 0
fi

command -v acquire_lock &>/dev/null && acquire_lock

# ── Staging scan ─────────────────────────────────────────────────────────────
# Mirrors prune_raw()'s predicate in extract_text.py exactly: same SKIP_NAMES, same
# derived-extension exclusions, same ">200 bytes" cache test. If the two drift, the
# job either loops forever or prunes something uncached.
scan_course() {
  "$PY" - "$1" <<'PY'
import sys, pathlib
staging = pathlib.Path(sys.argv[1])
cache = staging / "_text_cache"
SKIP = {"manifest.json", "_announcements.md", "courses.yaml"}
DERIVED = {".txt", ".md", ".json"}
raw = ready = 0
for p in sorted(staging.iterdir()):
    if not p.is_file() or p.name in SKIP or p.name.startswith("."):
        continue
    if p.suffix.lower() in DERIVED:
        continue
    raw += 1
    t = cache / (p.name + ".txt")
    if t.exists() and t.stat().st_size > 200:
        ready += 1
print(raw, ready)
PY
}

fail_count() { local f="$STATE_DIR/$1.fail"; [[ -f "$f" ]] && cat "$f" 2>/dev/null || echo 0; }
fail_age()   { local f="$STATE_DIR/$1.fail"; [[ -f "$f" ]] || { echo 999999; return; }
               echo $(( $(date +%s) - $(stat -f %m "$f" 2>/dev/null || echo 0) )); }
mark_fail()  { local c="$1"; echo $(( $(fail_count "$c") + 1 )) > "$STATE_DIR/$c.fail"; }
clear_fail() { rm -f "$STATE_DIR/$1.fail" "$STATE_DIR/$1.giveup"; }

pending=()
for dir in "$STAGING_ROOT"/*/; do
  [[ -d "$dir" ]] || continue
  course="$(basename "$dir")"
  read -r raw ready <<< "$(scan_course "$dir")"
  [[ "${raw:-0}" -gt 0 ]] && pending+=("$course")
done

if [[ ${#pending[@]} -eq 0 ]]; then
  exit 0
fi

echo "[canvas_sync] pending: ${pending[*]}"

for course in "${pending[@]}"; do
  dir="$STAGING_ROOT/$course"

  # Given up on this course — a human must look. Notified once, at the transition.
  [[ -f "$STATE_DIR/$course.giveup" ]] && { echo "[canvas_sync] $course: given up, skipping"; continue; }

  # Back off after a failure. Without this a failing course re-runs every 15 minutes,
  # and each retry re-sends un-cached media to Deepgram — a real bill, not just tokens.
  fails=$(fail_count "$course")
  if [[ "$fails" -gt 0 && "$(fail_age "$course")" -lt "$COOLDOWN_SECS" ]]; then
    echo "[canvas_sync] $course: in cooldown after $fails failure(s), skipping"
    continue
  fi

  if [[ "$fails" -ge "$MAX_FAILURES" ]]; then
    touch "$STATE_DIR/$course.giveup"
    echo "[canvas_sync] $course: $fails failures — giving up"
    [[ -x "$NOTIFY" ]] && "$NOTIFY" "Canvas sync stalled" "$course failed $fails times. Needs a look."
    continue
  fi

  # ── 1. Extract ─────────────────────────────────────────────────────────────
  echo "[canvas_sync] $course: extracting…"
  "$PY" "$EXTRACT" --course "$course" >> "$SYNTH_LOG" 2>&1

  read -r raw ready <<< "$(scan_course "$dir")"

  # Nothing extractable. Usually an unsupported type (.pptx) or a scanned PDF with
  # tesseract absent. Counting it as a failure is what stops the loop: the raw file
  # can never be pruned, so without the counter it would trigger this job forever.
  if [[ "${ready:-0}" -eq 0 ]]; then
    mark_fail "$course"
    echo "[canvas_sync] $course: $raw raw file(s), none extractable — no synthesis"
    continue
  fi

  # ── 2. Synthesize ──────────────────────────────────────────────────────────
  echo "[canvas_sync] $course: synthesizing $ready file(s)…"
  if ( cd "$SB_VAULT" && with_timeout "$SYNTH_TIMEOUT" \
        "$CLAUDE_BIN" -p "/coursenotes $course" ) >> "$SYNTH_LOG" 2>&1; then
    # ── 3. Prune — only on success ───────────────────────────────────────────
    # A failed synthesis must leave the raw files in place so the course stays
    # pending and retries. Pruning early would silently drop the work.
    "$PY" "$EXTRACT" --course "$course" --prune >> "$SYNTH_LOG" 2>&1
    clear_fail "$course"
    echo "[canvas_sync] $course: done"
    [[ -x "$NOTIFY" ]] && "$NOTIFY" "Canvas sync" "Notes ready: $course"
  else
    mark_fail "$course"
    echo "[canvas_sync] $course: synthesis failed (attempt $(fail_count "$course"))"
  fi
done

echo "[canvas_sync] done"

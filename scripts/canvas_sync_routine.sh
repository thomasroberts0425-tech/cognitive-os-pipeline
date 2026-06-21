#!/bin/zsh
# Cognitive OS — Canvas sync background routine (PHASE 2, opt-in)
#
# IMPORTANT ARCHITECTURE NOTE:
#   The Canvas FETCH step needs the Claude in Chrome MCP, which is an app<->extension
#   bridge and is NOT available to a headless `claude -p`. So this background job does
#   NOT fetch. It runs the headless-safe half: synthesize any already-staged materials
#   (left by an in-app /canvas-fetch or /loop) and prune raw files. Fetch stays in the
#   app via `/loop /canvas-sync <course>`.
#
#   A course has "pending synthesis" whenever raw (un-pruned) files remain in its
#   staging dir — after a successful sync those are deleted, leaving only _text_cache/.
#
# Enable:  launchctl load ~/Library/LaunchAgents/com.cognitiveos.canvassync.plist
# Disable: launchctl unload ~/Library/LaunchAgents/com.cognitiveos.canvassync.plist

set -uo pipefail
ROUTINE_NAME="canvas_sync"
source "$(dirname "$0")/common.sh" 2>/dev/null || true

STAGING_ROOT="$HOME/canvas_downloads"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
NOTIFY="$HOME/cognitive_os_pipeline/scripts/notify.sh"

[[ -x "$CLAUDE_BIN" ]] || { echo "[canvas_sync] claude binary not found at $CLAUDE_BIN" >&2; exit 0; }
[[ -d "$STAGING_ROOT" ]] || { echo "[canvas_sync] no staging root" >&2; exit 0; }

pending=()
for dir in "$STAGING_ROOT"/*/; do
  [[ -d "$dir" ]] || continue
  course="$(basename "$dir")"
  # count raw source files (exclude cache dir, manifest, announcements, dotfiles)
  raw_count=$(find "$dir" -maxdepth 1 -type f \
      ! -name 'manifest.json' ! -name '_announcements.md' ! -name '.*' \
      ! -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$raw_count" -gt 0 ]]; then
    pending+=("$course")
  fi
done

if [[ ${#pending[@]} -eq 0 ]]; then
  echo "[canvas_sync] nothing staged to synthesize — done"
  exit 0
fi

echo "[canvas_sync] pending synthesis for: ${pending[*]}"
for course in "${pending[@]}"; do
  echo "[canvas_sync] synthesizing $course (headless, --no-fetch)…"
  # Headless synthesis+prune. Chrome is NOT required for this half.
  "$CLAUDE_BIN" -p "/canvas-sync $course --no-fetch" \
      >> "$HOME/cognitive_os_pipeline/cache/canvas_sync.log" 2>&1 \
    && [[ -x "$NOTIFY" ]] && "$NOTIFY" "Canvas sync" "Synthesized & pruned: $course"
done
echo "[canvas_sync] done"

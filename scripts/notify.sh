#!/bin/zsh
# Cognitive OS — macOS notification helper
# Usage: notify.sh "Title" "Body message"
TITLE="${1:-Cognitive OS}"
BODY="${2:-Alert}"
osascript -e "display notification \"$BODY\" with title \"$TITLE\" sound name \"Glass\""

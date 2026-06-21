#!/usr/bin/env python3
"""
Deterministic Daily_Log index rewriter.
Scans 02_OPERATIONS/Daily_Notes/ and 02_OPERATIONS/Reviews/, then rewrites
the index sections in 02_OPERATIONS/Daily_Log.md between marker pairs.
No LLM — pure filesystem scan. Safe to run multiple times.
"""

import re
import sys
from pathlib import Path
from datetime import datetime

VAULT = Path("/Users/thomasroberts/Documents/NEW_COGNITIVE_OS")
DAILY_NOTES_DIR = VAULT / "02_OPERATIONS/Daily_Notes"
REVIEWS_DIR = VAULT / "02_OPERATIONS/Reviews"
DAILY_LOG = VAULT / "02_OPERATIONS/Daily_Log.md"

NOTES_START = "<!-- DAILY_INDEX_START -->"
NOTES_END = "<!-- DAILY_INDEX_END -->"
REVIEWS_START = "<!-- WEEKLY_REVIEWS_START -->"
REVIEWS_END = "<!-- WEEKLY_REVIEWS_END -->"


def get_daily_notes():
    if not DAILY_NOTES_DIR.exists():
        return []
    notes = sorted(
        [f for f in DAILY_NOTES_DIR.glob("*.md") if re.match(r"\d{4}-\d{2}-\d{2}\.md", f.name)],
        reverse=True
    )
    return notes


def get_weekly_reviews():
    if not REVIEWS_DIR.exists():
        return []
    reviews = sorted(
        [f for f in REVIEWS_DIR.glob("*.md") if re.match(r"\d{4}-W\d{2}\.md", f.name)],
        reverse=True
    )
    return reviews


def build_notes_block(notes):
    if not notes:
        return "No daily notes yet."
    lines = []
    for note in notes:
        stem = note.stem  # e.g. 2026-05-17
        lines.append(f"- [[{stem}]]")
    return "\n".join(lines)


def build_reviews_block(reviews):
    if not reviews:
        return "No weekly reviews yet."
    lines = []
    for review in reviews:
        stem = review.stem  # e.g. 2026-W21
        lines.append(f"- [[{stem}]]")
    return "\n".join(lines)


def replace_between_markers(content, start_marker, end_marker, new_block):
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL
    )
    replacement = f"{start_marker}\n{new_block}\n{end_marker}"
    if pattern.search(content):
        return pattern.sub(replacement, content)
    # Markers missing — append them
    return content + f"\n\n{replacement}\n"


def main():
    if not DAILY_LOG.exists():
        print(f"ERROR: Daily_Log.md not found at {DAILY_LOG}", file=sys.stderr)
        sys.exit(1)

    content = DAILY_LOG.read_text()

    notes = get_daily_notes()
    reviews = get_weekly_reviews()

    notes_block = build_notes_block(notes)
    reviews_block = build_reviews_block(reviews)

    # Replace or insert Daily Note Index
    content = replace_between_markers(content, NOTES_START, NOTES_END, notes_block)
    # Replace or insert Weekly Reviews
    content = replace_between_markers(content, REVIEWS_START, REVIEWS_END, reviews_block)

    # Update the updated: frontmatter field
    today = datetime.today().strftime("%Y-%m-%d")
    content = re.sub(r"^updated: .+$", f"updated: {today}", content, flags=re.MULTILINE)

    DAILY_LOG.write_text(content)
    print(f"Daily_Log updated: {len(notes)} daily notes, {len(reviews)} weekly reviews indexed")


if __name__ == "__main__":
    main()

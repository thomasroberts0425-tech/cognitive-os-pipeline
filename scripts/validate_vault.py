#!/usr/bin/env python3
"""
Vault integrity validator for Cognitive OS.
Runs nightly before evening synthesis, writes 00_SYSTEM/Validation_Report.md.
Checks: broken wikilinks, Entity_Registry integrity, stale state files,
missing INSERT markers, frontmatter schema drift, orphan AI chats.
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime, date

VAULT = Path(os.environ.get("COGNITIVE_OS_VAULT", "/Users/thomasroberts/Documents/NEW_COGNITIVE_OS"))
STATE_FILES_DIR = VAULT / "04_AI_CONTEXT/Project_States"
ENTITY_REGISTRY = VAULT / "00_SYSTEM/Entity_Registry.md"
VALIDATION_REPORT = VAULT / "00_SYSTEM/Validation_Report.md"
AI_CHATS_DIR = VAULT / "01_CAPTURE/AI_Chats"
DAILY_NOTES_DIR = VAULT / "02_OPERATIONS/Daily_Notes"

REQUIRED_FRONTMATTER_KEYS = ["type", "project", "current_objective", "status", "priority", "domain_tag", "updated"]
STATE_FILE_PATTERN = re.compile(r"_STATE\.md$")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]")
STALE_DAYS = 7
LARGE_FILE_MB = 5  # any .md bigger than this is almost certainly junk (e.g. a binary blob)

# Mirror Obsidian's userIgnoreFilters (.obsidian/app.json) so the validator's
# view matches the graph. Without this the validator scanned its OWN report —
# whose broken-link entries are written as `[[x]]` — and counted every one as a
# broken link, exploding total_issues into the thousands (false positives).
IGNORE_PREFIXES = ("05_ARCHIVE/", "06_TEMPLATES/", "00_SYSTEM/Templates/")
IGNORE_FILES = ("00_SYSTEM/Validation_Report.md",)
IGNORE_DIR_NAMES = {"graphify-out"}

FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
INLINE_CODE = re.compile(r"``[^`]*``|`[^`\n]*`")
FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def is_ignored(path):
    rel = str(path.relative_to(VAULT))
    return rel in IGNORE_FILES or rel.startswith(IGNORE_PREFIXES)


def strip_code(text):
    """Remove fenced/inline code and frontmatter so wikilinks inside them are
    NOT counted as links — Obsidian does not render code-wrapped [[links]]."""
    text = FRONTMATTER.sub("", text)
    text = FENCED_CODE.sub("", text)
    text = INLINE_CODE.sub("", text)
    return text


def get_all_md_files():
    """
    Walk the vault and return all .md files, skipping paths that cause
    OS-level deadlocks (e.g. iCloud .Trash, Mobile Documents symlinks).
    Uses an explicit walk instead of rglob so we can catch per-directory
    errors without aborting the entire scan.
    """
    SKIP_DIRS = {".Trash", ".git", "node_modules", ".obsidian", ".claude"} | IGNORE_DIR_NAMES
    results = []

    def _walk(path):
        try:
            entries = list(path.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                _walk(entry)
            elif entry.suffix == ".md":
                try:
                    # Verify the file is actually accessible before adding
                    entry.stat()
                    if not is_ignored(entry):
                        results.append(entry)
                except (OSError, PermissionError):
                    pass

    _walk(VAULT)
    return results


def check_large_files(all_files):
    """Flag oversized .md files — these are almost always junk (a binary blob
    saved with a .md extension, e.g. a video dumped by a fetcher)."""
    issues = []
    limit = LARGE_FILE_MB * 1_000_000
    for f in all_files:
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        if sz > limit:
            issues.append(f"  - `{f.relative_to(VAULT)}` — {sz/1_000_000:.0f} MB (likely a non-text blob; delete or relocate)")
    return issues


def build_basename_index(all_files):
    index = {}
    for f in all_files:
        index[f.stem.lower()] = f
        index[f.name.lower()] = f
    return index


def check_broken_wikilinks(all_files, basename_index):
    issues = []
    for f in all_files:
        content = strip_code(f.read_text(errors="replace"))
        links = WIKILINK_PATTERN.findall(content)
        for link in links:
            link_clean = link.strip()
            if not link_clean:
                continue
            # Skip external links, paths with slashes that are folder refs
            if link_clean.startswith("http") or "/" in link_clean:
                continue
            stem = Path(link_clean).stem.lower()
            name_lower = (link_clean + ".md").lower() if not link_clean.endswith(".md") else link_clean.lower()
            if stem not in basename_index and name_lower not in basename_index:
                rel = f.relative_to(VAULT)
                issues.append(f"  - `{rel}` → `[[{link_clean}]]` — no matching file")
    return issues


def check_entity_registry(basename_index):
    issues = []
    if not ENTITY_REGISTRY.exists():
        return ["  - Entity_Registry.md not found"]
    content = strip_code(ENTITY_REGISTRY.read_text(errors="replace"))
    links = WIKILINK_PATTERN.findall(content)
    for link in links:
        link_clean = link.strip()
        if not link_clean or "/" in link_clean:
            continue
        stem = Path(link_clean).stem.lower()
        if stem not in basename_index:
            issues.append(f"  - Entity_Registry: `[[{link_clean}]]` — no matching file (dangling row)")
    return issues


def check_stale_state_files():
    issues = []
    today = date.today()
    for f in STATE_FILES_DIR.glob("*_STATE.md"):
        content = f.read_text(errors="replace")
        m = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})", content, re.MULTILINE)
        if m:
            updated = date.fromisoformat(m.group(1))
            age = (today - updated).days
            if age > STALE_DAYS:
                issues.append(f"  - `{f.name}` — last updated {age} days ago ({m.group(1)})")
        else:
            issues.append(f"  - `{f.name}` — missing `updated:` frontmatter field")
    return issues


def check_missing_markers():
    issues = []
    if not DAILY_NOTES_DIR.exists():
        return []
    for f in sorted(DAILY_NOTES_DIR.glob("*.md"))[-7:]:  # last 7 notes only
        content = f.read_text(errors="replace")
        if "<!-- BRIEFING_START -->" not in content and "<!-- BRIEFING_END -->" not in content:
            issues.append(f"  - `{f.name}` — missing BRIEFING marker pair")
        if "<!-- EVENING_START -->" not in content and "<!-- EVENING_END -->" not in content:
            issues.append(f"  - `{f.name}` — missing EVENING marker pair (may be today's note)")
    return issues


def check_frontmatter_schema():
    issues = []
    for f in STATE_FILES_DIR.glob("*_STATE.md"):
        if f.name == "Vault_Migration_Report.md":
            continue
        content = f.read_text(errors="replace")
        fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not fm_match:
            issues.append(f"  - `{f.name}` — no frontmatter block found")
            continue
        fm = fm_match.group(1)
        for key in REQUIRED_FRONTMATTER_KEYS:
            if not re.search(rf"^{key}:", fm, re.MULTILINE):
                issues.append(f"  - `{f.name}` — missing required key `{key}:`")
    return issues


def check_orphan_ai_chats():
    issues = []
    if not AI_CHATS_DIR.exists():
        return []
    state_pattern = re.compile(r"\[\[.*?_STATE(?:\.md)?\]\]", re.IGNORECASE)
    for f in AI_CHATS_DIR.glob("*.md"):
        content = f.read_text(errors="replace")
        if not state_pattern.search(content):
            issues.append(f"  - `{f.name}` — no link to any *_STATE.md file")
    return issues


def main():
    print("Running vault validation...")
    all_files = get_all_md_files()
    basename_index = build_basename_index(all_files)
    today_str = datetime.today().strftime("%Y-%m-%d %H:%M")

    broken_links = check_broken_wikilinks(all_files, basename_index)
    registry_issues = check_entity_registry(basename_index)
    stale_states = check_stale_state_files()
    missing_markers = check_missing_markers()
    schema_issues = check_frontmatter_schema()
    orphan_chats = check_orphan_ai_chats()
    large_files = check_large_files(all_files)

    total_issues = sum(len(x) for x in [broken_links, registry_issues, stale_states,
                                          missing_markers, schema_issues, orphan_chats,
                                          large_files])

    def section(title, items):
        if not items:
            return f"### {title}\n✅ No issues\n"
        return f"### {title}\n" + "\n".join(items) + "\n"

    report = f"""---
type: validation_report
generated: {today_str}
total_issues: {total_issues}
tags: [system/validation]
---

# 🔍 Vault Validation Report

Generated: {today_str} | Total issues: {total_issues}

{"⚠️ Issues found — review before morning synthesis." if total_issues > 0 else "✅ Vault clean."}

---

{section("Broken Wikilinks", broken_links)}
{section("Entity Registry Integrity", registry_issues)}
{section("Stale State Files (>%d days)" % STALE_DAYS, stale_states)}
{section("Missing Insertion Markers", missing_markers)}
{section("Frontmatter Schema Drift", schema_issues)}
{section("Orphan AI Chats (no state file link)", orphan_chats)}
{section("Oversized Files (>%d MB)" % LARGE_FILE_MB, large_files)}
"""

    VALIDATION_REPORT.write_text(report)
    print(f"Validation complete: {total_issues} issue(s) found → {VALIDATION_REPORT}")
    return 0 if total_issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

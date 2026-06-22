#!/usr/bin/env python3
"""
Study Guide builder for the Canvas study pipeline (Layer 4).

Deterministic helper behind the `studyguide` skill. Turns finalized vault notes
into an exam-prep study guide. Owns: section resolution, note->cache mapping, the
spaced/interleaved study schedule, flashcard->Quizlet export, artifact contract
checks, and the validate_vault.py gate. Generation is done by Claude subagents;
this script never calls an LLM.

Subcommands: plan | assemble | finalize  (see the studyguide SKILL.md for order).
Stdlib only (no PyYAML, no pip deps). Python 3.9+.
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Files in a course vault folder that are hubs/packages/outputs, never "sections":
HUB_SUFFIXES = (" Syllabus.md", "-Reading-Materials.md", "-Notes-Package.md")
OUTPUT_MARKERS = ("Study-Guide", "Flashcards", "Practice-MCQs", "Essay-Practice")
MODULE_RE = re.compile(r"M(\d{1,2})\b")
RANGE_RE = re.compile(r"^M(\d{1,2})-M(\d{1,2})$")
SINGLE_RE = re.compile(r"^M(\d{1,2})$")


def module_num(name: str):
    """Extract the M<NN> module number from a note filename, or None."""
    m = MODULE_RE.search(name)
    return int(m.group(1)) if m else None


def _is_excluded(name: str) -> bool:
    if name.endswith(HUB_SUFFIXES):
        return True
    return any(marker in name for marker in OUTPUT_MARKERS)


def resolve_sections(vault_dir: Path, course: str, range_spec: str):
    """Return sorted note Paths in vault_dir matching range_spec.

    range_spec: 'all' | 'M07-M12' | 'M08'. Excludes hub/package/output files.
    Raises ValueError on a malformed range.
    """
    candidates = sorted(
        p for p in vault_dir.glob(f"{course}*.md") if not _is_excluded(p.name)
    )
    if range_spec == "all":
        return candidates

    rng = RANGE_RE.match(range_spec)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        return [p for p in candidates
                if (n := module_num(p.name)) is not None and lo <= n <= hi]

    one = SINGLE_RE.match(range_spec)
    if one:
        target = int(one.group(1))
        return [p for p in candidates if module_num(p.name) == target]

    raise ValueError(f"Bad range spec: {range_spec!r} (use 'all', 'M07-M12', or 'M08')")


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def read_frontmatter(text: str) -> dict:
    """Minimal `key: value` frontmatter parser (NOT full YAML). Strips quotes."""
    out = {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("-"):
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def sources_for_note(note_path: Path, cache_dir: Path):
    """Map a note's `source:` frontmatter to existing cache .txt files."""
    fm = read_frontmatter(note_path.read_text(encoding="utf-8", errors="ignore"))
    raw = fm.get("source", "")
    found = []
    for piece in re.split(r"[;,]", raw):
        stem = Path(piece.strip()).stem
        if not stem:
            continue
        cand = cache_dir / f"{stem}.txt"
        if cand.exists():
            found.append(cand)
    return found


PASSES = ["Understand", "Retrieve", "Apply"]
# Day offset from the PREVIOUS session. Expands then plateaus at 7 (2-3-5-7 style).
GAP_PATTERN = [0, 1, 2, 3, 5, 7]


def _gap(i: int) -> int:
    return GAP_PATTERN[i] if i < len(GAP_PATTERN) else GAP_PATTERN[-1]


def build_schedule(modules, per_session: int = 3):
    """Spaced + interleaved 3-pass schedule. See Interfaces for the contract."""
    activities = [{"module": m, "pass": p} for p in PASSES for m in modules]
    sessions = []
    for idx in range(0, len(activities), per_session):
        chunk = activities[idx:idx + per_session]
        ordinal = len(sessions)
        sessions.append({
            "ordinal": ordinal + 1,
            "gap_days": _gap(ordinal),
            "activities": chunk,
            "passes": sorted({a["pass"] for a in chunk}),
        })
    return sessions


def place_dates(sessions, exam: date, today: date) -> bool:
    """Assign each session a calendar date. Returns True if crunch mode was used."""
    last_day = exam - timedelta(days=1)
    # Cumulative offset of each session from session 1 (gap of session 1 ignored).
    cum, running = [], 0
    for i, s in enumerate(sessions):
        running += s["gap_days"] if i > 0 else 0
        cum.append(running)
    span = cum[-1] if cum else 0
    start = last_day - timedelta(days=span)
    available = (last_day - today).days      # max day-offset that fits from today

    if start >= today:                       # normal: full gaps fit, anchor end at exam-1
        for s, off in zip(sessions, cum):
            s["date"] = (start + timedelta(days=off)).isoformat()
        return False

    # Full gaps overflow. Try compressing to 1 day per session and still anchor
    # the last session at exam-1 (still "normal" — no crunch flag).
    n = len(sessions)
    if n - 1 <= available:                    # one day each fits in the window
        for i, s in enumerate(sessions):
            off = (n - 1) - i                 # days before exam-1
            s["date"] = (last_day - timedelta(days=off)).isoformat()
        return False

    # Crunch: pack forward from today, gaps capped at 1 (then 0), clamp at exam-1.
    cur = today
    for i, s in enumerate(sessions):
        if i > 0:
            cur = min(cur + timedelta(days=1 if s["gap_days"] else 0), last_day)
        s["date"] = max(cur, today).isoformat()
    return True


def render_schedule_md(sessions, dated: bool, crunch: bool) -> str:
    lines = ["## Optimal Study Path",
             "",
             "_Spaced + interleaved, built on retrieval & distributed practice "
             "(the only two 'high-utility' techniques). Each session mixes passes/modules._",
             ""]
    if crunch:
        lines += ["> [!warning] Crunch schedule",
                  "> Not enough runway before the exam — intervals compressed. "
                  "Prioritise the Retrieve and Apply passes.", ""]
    header = "| Session | " + ("Date | " if dated else "") + "Focus | Activities |"
    sep = "|---|" + ("---|" if dated else "") + "---|---|"
    lines += [header, sep]
    for s in sessions:
        acts = "; ".join(f"{a['pass']}: {a['module']}" for a in s["activities"])
        focus = ", ".join(s["passes"])
        datecol = f" {s.get('date','')} |" if dated else ""
        lines.append(f"| {s['ordinal']} |{datecol} {focus} | {acts} |")
    return "\n".join(lines) + "\n"

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

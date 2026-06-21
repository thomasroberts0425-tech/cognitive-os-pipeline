# Study Guide Layer (Layer 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/studyguide <COURSE> <range>` layer that turns finalized vault notes into an evidence-based study guide — spaced study path + atomic flashcards (Quizlet-exportable) + scenario MCQs + IRAC essay practice.

**Architecture:** Mirror the `coursenotes` pattern (Approach A). A stdlib-only Python helper `build_studyguide.py` owns everything deterministic (section resolution, note→cache mapping, the spaced/interleaved schedule, flashcard→Quizlet export, contract checks, the `validate_vault.py` gate). One Claude subagent per module owns content generation; a condenser subagent assembles the master guide. The `studyguide` SKILL.md orchestrates: `plan` → per-module subagents → `assemble` → condenser → `finalize`.

**Tech Stack:** Python 3.9 (stdlib only — `argparse`, `json`, `re`, `pathlib`, `datetime`; **no PyYAML, no pip installs**). Tests: `unittest` (`python3 -m unittest`). Existing helpers reused: `scripts/validate_vault.py`. Obsidian-markdown outputs. Quizlet plain-text import.

## Global Constraints

Every task implicitly includes these (copied verbatim from the spec):

- **Study aid only, never submittable prose.** Every generated output carries `ai_study_aid: true`. Essay practice ships scaffolds + rubrics only — **never model essays** (UofT `ai_study_aid` constraint).
- **Zero broken wikilinks.** `python3 ~/cognitive_os_pipeline/scripts/validate_vault.py` is the gate (exit 0 = clean, exit 1 = issues). Only emit `[[links]]` to files that already exist or that this run creates.
- **Notes are the spine; `_text_cache` (transcripts/PDFs) is for verification + depth.** No re-fetch, no Deepgram calls — operate on already-synthesized notes + the durable cache only.
- **Idempotent.** Re-running for the same range overwrites the `StudyGuide-<range>` folder; never corrupts the vault.
- **Stdlib only.** No new Python dependencies (the box is a 2020 Intel i3 / 8 GB; `pip` deps are avoided). Python is `/usr/bin/python3` (3.9.6).
- **Helpers never parse `courses.yaml`.** The SKILL reads `courses.yaml` and passes `--vault-dir`, `--subject-type`, etc. as args (matches `extract_text.py`).
- **Per-module isolation.** One subagent per module so a single OOM/failure never loses completed modules (idempotent resume).

**Key paths:**
- Helper: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Tests: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`
- Skill: `~/.claude/skills/studyguide/SKILL.md`
- Vault root: `/Users/thomasroberts/Documents/NEW_COGNITIVE_OS`
- Staging/cache: `~/canvas_downloads/<COURSE>/_text_cache/<name>.txt`
- Outputs: `<vault_root>/<vault_dir>/StudyGuide-<rangeslug>/`

**Per-module artifact contract** (what each subagent writes; the helper parses/validates it). One file per module at `StudyGuide-<rangeslug>/<Course>-<rangeslug>-M<NN>.md` with these exact `## ` sections:
- `## Flashcards` — lines of the form `Term :: Definition` (one atomic fact per line; the `::` delimiter doubles as the Obsidian spaced-repetition inline format and the Quizlet-export parse source).
- `## Practice MCQs` — each item has a scenario stem, options, an answer key, a per-distractor `Rationale:`, and a `Bloom:` tag.
- `## Essay Practice` — IRAC prompt(s) + issue-spotting checklist + skeleton + `Rubric:`. **No** `Model Answer` / `Sample Essay` / `Model Essay` prose.
- `## Why-It's-True Prompts` — elaborative-interrogation "why is this true / why this rule here?" prompts.
- Frontmatter includes `ai_study_aid: true`.

---

### Task 1: Test scaffold + section resolution (`resolve_sections`)

**Files:**
- Create: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Create: `~/cognitive_os_pipeline/tests/__init__.py` (empty)
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `resolve_sections(vault_dir: Path, course: str, range_spec: str) -> list[Path]` — sorted note paths for the range. `range_spec` is `"all"`, `"M07-M12"`, or `"M08"`. Excludes hub/output files. Raises `ValueError` on malformed range. Also `module_num(name: str) -> int | None` (extracts the `M<NN>` number from a filename).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_studyguide.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_studyguide as bsg


class TestResolveSections(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        for n in ["IRE430-M07-Human-Rights.md", "IRE430-M08-BFOR.md",
                  "IRE430-M12-Privacy.md", "IRE430-Reading-Materials.md",
                  "IRE430 Syllabus.md", "IRE430-Notes-Package.md"]:
            (self.tmp / n).write_text("---\nai_study_aid: true\n---\n")

    def test_range_selects_inclusive_and_excludes_hubs(self):
        got = sorted(p.name for p in bsg.resolve_sections(self.tmp, "IRE430", "M07-M12"))
        self.assertEqual(got, ["IRE430-M07-Human-Rights.md", "IRE430-M08-BFOR.md",
                               "IRE430-M12-Privacy.md"])

    def test_all_excludes_hub_and_package_files(self):
        got = sorted(p.name for p in bsg.resolve_sections(self.tmp, "IRE430", "all"))
        self.assertNotIn("IRE430-Reading-Materials.md", got)
        self.assertNotIn("IRE430 Syllabus.md", got)
        self.assertNotIn("IRE430-Notes-Package.md", got)
        self.assertEqual(len(got), 3)

    def test_single_module(self):
        got = [p.name for p in bsg.resolve_sections(self.tmp, "IRE430", "M08")]
        self.assertEqual(got, ["IRE430-M08-BFOR.md"])

    def test_bad_range_raises(self):
        with self.assertRaises(ValueError):
            bsg.resolve_sections(self.tmp, "IRE430", "chapter7")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_studyguide'` (and `tests/__init__.py` missing).

- [ ] **Step 3: Create `tests/__init__.py` and the helper with `resolve_sections`**

Create `tests/__init__.py` as an empty file. Then create `scripts/build_studyguide.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/__init__.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): section resolution helper + test scaffold"
```

---

### Task 2: Note → cache-source mapping (`sources_for_note`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `sources_for_note(note_path: Path, cache_dir: Path) -> list[Path]` — reads the note's `source:` frontmatter value, splits on `;`/`,`, and returns the existing `<cache_dir>/<stem>.txt` files. Returns `[]` if none found (notes-as-spine fallback). Also `read_frontmatter(text: str) -> dict` (minimal `key: value` parser — **not** full YAML).

- [ ] **Step 1: Write the failing test**

```python
class TestSourcesForNote(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = self.tmp / "_text_cache"
        self.cache.mkdir()
        (self.cache / "Ch21 Intro.txt").write_text("transcript")
        (self.cache / "Ch22 Grounds.txt").write_text("transcript")
        self.note = self.tmp / "IRE430-M07-Human-Rights.md"
        self.note.write_text(
            '---\ntitle: "x"\nsource: "Ch21 Intro.mp3; Ch22 Grounds.mp3"\n'
            "ai_study_aid: true\n---\n# body\n")

    def test_maps_source_filenames_to_existing_cache_txt(self):
        got = sorted(p.name for p in bsg.sources_for_note(self.note, self.cache))
        self.assertEqual(got, ["Ch21 Intro.txt", "Ch22 Grounds.txt"])

    def test_missing_cache_returns_empty(self):
        n = self.tmp / "IRE430-M99-Ghost.md"
        n.write_text('---\nsource: "Nope.pdf"\n---\n')
        self.assertEqual(bsg.sources_for_note(n, self.cache), [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestSourcesForNote -v`
Expected: FAIL — `AttributeError: module 'build_studyguide' has no attribute 'sources_for_note'`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestSourcesForNote -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): map note source frontmatter to cache files"
```

---

### Task 3: Ordinal spaced+interleaved schedule (`build_schedule`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `build_schedule(modules: list[str], per_session: int = 3) -> list[dict]`. Each session dict: `{"ordinal": int, "gap_days": int, "activities": [{"module": str, "pass": str}], "passes": [str]}`. Algorithm: emit all `Understand` (cycling modules), then all `Retrieve`, then all `Apply` — this guarantees each module's Retrieve/Apply land well after its Understand (spacing) and modules interleave within each phase. Group activities into sessions of `per_session`; assign expanding gaps from `GAP_PATTERN`.
- Consumes: nothing from prior tasks.

- [ ] **Step 1: Write the failing test**

```python
class TestBuildSchedule(unittest.TestCase):
    def test_passes_are_phased_and_spaced(self):
        sessions = bsg.build_schedule(["M07", "M08"], per_session=2)
        flat = [(a["module"], a["pass"]) for s in sessions for a in s["activities"]]
        # all Understand come before any Retrieve; all Retrieve before any Apply
        order = [p for _, p in flat]
        self.assertEqual(order, ["Understand", "Understand",
                                 "Retrieve", "Retrieve", "Apply", "Apply"])
        # each module appears once per pass
        self.assertEqual(flat.count(("M07", "Understand")), 1)
        self.assertEqual(flat.count(("M08", "Apply")), 1)

    def test_sessions_grouped_and_gaps_expand(self):
        sessions = bsg.build_schedule(["M07", "M08", "M09"], per_session=3)
        self.assertEqual([s["ordinal"] for s in sessions], [1, 2, 3])
        self.assertEqual(sessions[0]["gap_days"], 0)   # first session: today
        self.assertTrue(sessions[1]["gap_days"] <= sessions[2]["gap_days"])  # expanding
        self.assertEqual(len(sessions[0]["activities"]), 3)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestBuildSchedule -v`
Expected: FAIL — no attribute `build_schedule`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestBuildSchedule -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): spaced+interleaved 3-pass schedule builder"
```

---

### Task 4: Calendar placement + crunch mode (`place_dates`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `place_dates(sessions: list[dict], exam: date, today: date) -> bool`. Mutates each session in place adding `"date": "YYYY-MM-DD"`. Returns `crunch` (bool). Normal mode: the **last** session lands the day before the exam; earlier sessions are back-filled by their cumulative gaps. If the back-filled start is before `today`, switch to **crunch**: place sessions forward from `today` with gaps capped at 1 day (then 0 if still overflowing), never past `exam - 1`. Returns `True` in crunch mode.
- Consumes: `build_schedule` output shape (`gap_days`, `ordinal`).

- [ ] **Step 1: Write the failing test**

```python
class TestPlaceDates(unittest.TestCase):
    def test_normal_mode_ends_day_before_exam(self):
        sessions = bsg.build_schedule(["M07", "M08", "M09"], per_session=1)  # 9 sessions
        crunch = bsg.place_dates(sessions, exam=date(2026, 8, 1), today=date(2026, 7, 1))
        self.assertFalse(crunch)
        self.assertEqual(sessions[-1]["date"], "2026-07-31")   # exam - 1
        self.assertTrue(all("date" in s for s in sessions))

    def test_crunch_when_not_enough_runway(self):
        sessions = bsg.build_schedule(["M07", "M08", "M09"], per_session=1)
        crunch = bsg.place_dates(sessions, exam=date(2026, 7, 5), today=date(2026, 7, 1))
        self.assertTrue(crunch)
        # everything fits within [today, exam-1]
        for s in sessions:
            self.assertGreaterEqual(s["date"], "2026-07-01")
            self.assertLessEqual(s["date"], "2026-07-04")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestPlaceDates -v`
Expected: FAIL — no attribute `place_dates`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
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

    if start >= today:                       # normal: anchor end at exam-1
        for s, off in zip(sessions, cum):
            s["date"] = (start + timedelta(days=off)).isoformat()
        return False

    # Crunch: pack forward from today, gaps capped at 1, clamp at exam-1.
    cur = today
    for i, s in enumerate(sessions):
        if i > 0:
            cur = min(cur + timedelta(days=1 if s["gap_days"] else 0), last_day)
        s["date"] = max(cur, today).isoformat()
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestPlaceDates -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): calendar placement with crunch fallback"
```

---

### Task 5: Schedule markdown rendering (`render_schedule_md`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `render_schedule_md(sessions: list[dict], dated: bool, crunch: bool) -> str` — a markdown section ("## Optimal Study Path") with a table of sessions (ordinal, date column only when `dated`, activities, passes) and a one-line crunch warning when `crunch`.
- Consumes: session dicts from Tasks 3–4.

- [ ] **Step 1: Write the failing test**

```python
class TestRenderSchedule(unittest.TestCase):
    def test_ordinal_only_has_no_date_column(self):
        sessions = bsg.build_schedule(["M07", "M08"], per_session=2)
        md = bsg.render_schedule_md(sessions, dated=False, crunch=False)
        self.assertIn("## Optimal Study Path", md)
        self.assertIn("Understand", md)
        self.assertNotIn("Date", md)

    def test_dated_includes_date_and_crunch_warning(self):
        sessions = bsg.build_schedule(["M07", "M08"], per_session=2)
        bsg.place_dates(sessions, exam=date(2026, 7, 3), today=date(2026, 7, 1))
        md = bsg.render_schedule_md(sessions, dated=True, crunch=True)
        self.assertIn("Date", md)
        self.assertIn("crunch", md.lower())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestRenderSchedule -v`
Expected: FAIL — no attribute `render_schedule_md`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestRenderSchedule -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): render study-path schedule to markdown"
```

---

### Task 6: Flashcard parsing + Quizlet export (`parse_flashcards`, `write_quizlet`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces:
  - `parse_flashcards(text: str) -> list[tuple[str, str]]` — extracts `Term :: Definition` pairs found under a `## Flashcards` heading; stops at the next `## ` heading; tolerates an optional leading `- `/`* `.
  - `write_quizlet(cards: list[tuple[str, str]], out_path: Path) -> int` — writes Quizlet import text (TAB between term/def, newline between cards). Sanitises tabs/newlines inside fields to spaces. Returns the card count.
- Consumes: the per-module artifact contract (`## Flashcards`, `Term :: Definition`).

- [ ] **Step 1: Write the failing test**

```python
class TestFlashcards(unittest.TestCase):
    ARTIFACT = (
        "## Why-It's-True Prompts\n- Why does BFOR apply?\n\n"
        "## Flashcards\n"
        "- BFOR :: Bona fide occupational requirement; a defence to discrimination\n"
        "Meiorin test :: 3-step test for a BFOR\n\n"
        "## Practice MCQs\n- not a card :: should be ignored\n")

    def test_parse_only_flashcards_section(self):
        cards = bsg.parse_flashcards(self.ARTIFACT)
        self.assertEqual(cards, [
            ("BFOR", "Bona fide occupational requirement; a defence to discrimination"),
            ("Meiorin test", "3-step test for a BFOR")])

    def test_write_quizlet_tab_and_sanitises(self):
        import tempfile
        out = Path(tempfile.mkdtemp()) / "q.txt"
        n = bsg.write_quizlet([("A\tB", "line1\nline2")], out)
        self.assertEqual(n, 1)
        content = out.read_text()
        self.assertEqual(content.strip(), "A B\tline1 line2")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestFlashcards -v`
Expected: FAIL — no attribute `parse_flashcards`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
CARD_RE = re.compile(r"^\s*(?:[-*]\s+)?(.+?)\s*::\s*(.+?)\s*$")


def parse_flashcards(text: str):
    """Extract Term :: Definition pairs under the '## Flashcards' heading only."""
    cards, in_section = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## Flashcards"
            continue
        if in_section:
            m = CARD_RE.match(line)
            if m:
                cards.append((m.group(1), m.group(2)))
    return cards


def _clean(field: str) -> str:
    return field.replace("\t", " ").replace("\n", " ").strip()


def write_quizlet(cards, out_path: Path) -> int:
    rows = [f"{_clean(t)}\t{_clean(d)}" for t, d in cards]
    out_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return len(cards)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestFlashcards -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): flashcard parsing + Quizlet export"
```

---

### Task 7: Artifact contract / compliance checks (`check_artifacts`)

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `check_artifacts(paths: list[Path]) -> list[str]` — returns a list of human-readable issues (empty = clean). Each artifact must: contain all required `## ` sections; have `Rationale:` and `Bloom:` in the MCQ section; have `Rubric:` in the essay section; **not** contain banned model-essay markers (`model answer`, `model essay`, `sample essay`, case-insensitive); have `ai_study_aid: true` in frontmatter.
- Consumes: the per-module artifact contract.

- [ ] **Step 1: Write the failing test**

```python
class TestCheckArtifacts(unittest.TestCase):
    GOOD = (
        '---\nai_study_aid: true\n---\n'
        "## Why-It's-True Prompts\n- Why?\n"
        "## Flashcards\nA :: B\n"
        "## Practice MCQs\nQ... Answer: C. Rationale: ... Bloom: Apply\n"
        "## Essay Practice\nPrompt... Rubric: 10 pts issues...\n")

    def _write(self, text):
        import tempfile
        p = Path(tempfile.mkdtemp()) / "IRE430-M07-M12-M07.md"
        p.write_text(text)
        return p

    def test_clean_artifact_has_no_issues(self):
        self.assertEqual(bsg.check_artifacts([self._write(self.GOOD)]), [])

    def test_model_essay_is_flagged(self):
        bad = self.GOOD + "\n## Essay Practice\nModel Answer: In conclusion the worker...\n"
        issues = bsg.check_artifacts([self._write(bad)])
        self.assertTrue(any("model" in i.lower() for i in issues))

    def test_missing_section_and_aid_flag_flagged(self):
        bad = "## Flashcards\nA :: B\n"  # no frontmatter, missing sections
        issues = bsg.check_artifacts([self._write(bad)])
        self.assertTrue(any("ai_study_aid" in i for i in issues))
        self.assertTrue(any("Practice MCQs" in i for i in issues))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestCheckArtifacts -v`
Expected: FAIL — no attribute `check_artifacts`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
REQUIRED_SECTIONS = ["## Flashcards", "## Practice MCQs",
                     "## Essay Practice", "## Why-It's-True Prompts"]
BANNED_ESSAY = ("model answer", "model essay", "sample essay")


def check_artifacts(paths):
    """Validate per-module artifacts against the contract. Returns issue strings."""
    issues = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        name = p.name
        for sec in REQUIRED_SECTIONS:
            if sec not in text:
                issues.append(f"{name}: missing section {sec}")
        if "## Practice MCQs" in text:
            if "rationale:" not in low:
                issues.append(f"{name}: MCQs missing 'Rationale:'")
            if "bloom:" not in low:
                issues.append(f"{name}: MCQs missing 'Bloom:' tag")
        if "## Essay Practice" in text and "rubric:" not in low:
            issues.append(f"{name}: essay section missing 'Rubric:'")
        for banned in BANNED_ESSAY:
            if banned in low:
                issues.append(f"{name}: contains banned model-essay marker '{banned}'")
        if read_frontmatter(text).get("ai_study_aid") != "true":
            issues.append(f"{name}: missing 'ai_study_aid: true' in frontmatter")
    return issues
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestCheckArtifacts -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): artifact contract + study-aid compliance checks"
```

---

### Task 8: `plan` subcommand — emit briefs + schedule + `_plan.json`

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces: `cmd_plan(args) -> int` and `range_slug(range_spec) -> str` (`"M07-M12"`→`"M07-M12"`, `"all"`→`"all"`). `cmd_plan` resolves sections, builds the schedule (+dates if `--exam`), writes `StudyGuide-<slug>/_schedule.md` and `StudyGuide-<slug>/_plan.json` (list of briefs: `{module, note_path, output_path, cache_sources}` + `meta`), and prints `_plan.json` to stdout. Returns 0 (or 2 if no sections matched).
- Consumes: `resolve_sections`, `sources_for_note`, `build_schedule`, `place_dates`, `render_schedule_md`.

- [ ] **Step 1: Write the failing test**

```python
class TestCmdPlan(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp())
        self.vdir = self.root / "vault" / "IRE430"
        self.vdir.mkdir(parents=True)
        self.cache = self.root / "staging" / "IRE430" / "_text_cache"
        self.cache.mkdir(parents=True)
        (self.cache / "Ch21.txt").write_text("t")
        (self.vdir / "IRE430-M07-HR.md").write_text(
            '---\nsource: "Ch21.mp3"\nai_study_aid: true\n---\n')
        (self.vdir / "IRE430 Syllabus.md").write_text("---\n---\n")

    def _args(self, **kw):
        import argparse
        d = dict(course="IRE430", range="M07-M07", vault_dir=str(self.vdir),
                 staging_root=str(self.root / "staging"), exam=None)
        d.update(kw)
        return argparse.Namespace(**d)

    def test_plan_writes_plan_json_and_schedule(self):
        rc = bsg.cmd_plan(self._args())
        self.assertEqual(rc, 0)
        sg = self.vdir / "StudyGuide-M07-M07"
        plan = json.loads((sg / "_plan.json").read_text())
        self.assertEqual(len(plan["briefs"]), 1)
        self.assertTrue(plan["briefs"][0]["cache_sources"])  # Ch21.txt mapped
        self.assertTrue((sg / "_schedule.md").exists())
        self.assertFalse(plan["meta"]["dated"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestCmdPlan -v`
Expected: FAIL — no attribute `cmd_plan`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
def range_slug(range_spec: str) -> str:
    return range_spec


def cmd_plan(args) -> int:
    vault_dir = Path(args.vault_dir)
    course = args.course
    slug = range_slug(args.range)
    notes = resolve_sections(vault_dir, course, args.range)
    if not notes:
        print(f"No sections matched {args.range!r} for {course}", file=sys.stderr)
        return 2

    cache_dir = Path(args.staging_root) / course / "_text_cache"
    sg_dir = vault_dir / f"StudyGuide-{slug}"
    sg_dir.mkdir(parents=True, exist_ok=True)

    briefs, modules = [], []
    for note in notes:
        mnum = module_num(note.name)
        mtag = f"M{mnum:02d}" if mnum is not None else note.stem
        modules.append(mtag)
        briefs.append({
            "module": mtag,
            "note_path": str(note),
            "output_path": str(sg_dir / f"{course}-{slug}-{mtag}.md"),
            "cache_sources": [str(c) for c in sources_for_note(note, cache_dir)],
        })

    sessions = build_schedule(modules)
    dated = bool(args.exam)
    crunch = False
    if dated:
        exam = date.fromisoformat(args.exam)
        crunch = place_dates(sessions, exam, date.today())
    (sg_dir / "_schedule.md").write_text(
        render_schedule_md(sessions, dated=dated, crunch=crunch), encoding="utf-8")

    plan = {"meta": {"course": course, "range": args.range, "slug": slug,
                     "studyguide_dir": str(sg_dir), "dated": dated, "crunch": crunch},
            "briefs": briefs}
    (sg_dir / "_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps(plan, indent=2))
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestCmdPlan -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): plan subcommand emits briefs + schedule"
```

---

### Task 9: `assemble` + `finalize` subcommands + CLI dispatch

**Files:**
- Modify: `~/cognitive_os_pipeline/scripts/build_studyguide.py`
- Test: `~/cognitive_os_pipeline/tests/test_build_studyguide.py`

**Interfaces:**
- Produces:
  - `cmd_assemble(args) -> int` — reads `_plan.json`; runs `check_artifacts` on the per-module artifacts (returns 1 and prints issues if any); then parses flashcards from all artifacts and writes `<Course>-<slug>-Flashcards.md` (vault, `Term :: Definition` lines) and `<Course>-<slug>-Flashcards-quizlet.txt`. Returns 0 on success.
  - `cmd_finalize(args) -> int` — runs `validate_vault.py` as the broken-link gate (subprocess); writes the insight brief to `01_CAPTURE/Inbox/<Course>-studyguide-<date>.md`. Returns the validator's exit code.
  - `main()` — argparse with subcommands `plan|assemble|finalize`, dispatching to the above. Add `if __name__ == "__main__": sys.exit(main())`.
- Consumes: `parse_flashcards`, `write_quizlet`, `check_artifacts`, `cmd_plan` output (`_plan.json`).

- [ ] **Step 1: Write the failing test**

```python
class TestAssemble(unittest.TestCase):
    def setUp(self):
        import tempfile, argparse
        self.vdir = Path(tempfile.mkdtemp()) / "IRE430"
        self.sg = self.vdir / "StudyGuide-M07-M07"
        self.sg.mkdir(parents=True)
        art = self.sg / "IRE430-M07-M07-M07.md"
        art.write_text(
            '---\nai_study_aid: true\n---\n'
            "## Why-It's-True Prompts\n- Why?\n"
            "## Flashcards\nBFOR :: a defence\n"
            "## Practice MCQs\nQ Answer: A Rationale: x Bloom: Apply\n"
            "## Essay Practice\nPrompt Rubric: pts\n")
        (self.sg / "_plan.json").write_text(json.dumps({
            "meta": {"course": "IRE430", "slug": "M07-M07",
                     "studyguide_dir": str(self.sg)},
            "briefs": [{"module": "M07", "output_path": str(art)}]}))
        self.args = argparse.Namespace(plan_json=str(self.sg / "_plan.json"))

    def test_assemble_writes_flashcards_and_quizlet(self):
        rc = bsg.cmd_assemble(self.args)
        self.assertEqual(rc, 0)
        self.assertTrue((self.vdir / "StudyGuide-M07-M07" /
                         "IRE430-M07-M07-Flashcards-quizlet.txt").exists())
        vault_cards = (self.vdir / "StudyGuide-M07-M07" /
                       "IRE430-M07-M07-Flashcards.md").read_text()
        self.assertIn("BFOR :: a defence", vault_cards)

    def test_assemble_blocks_on_contract_violation(self):
        art = Path(json.loads(Path(self.args.plan_json).read_text())
                   ["briefs"][0]["output_path"])
        art.write_text("## Flashcards\nA :: B\n")  # missing sections + aid flag
        self.assertEqual(bsg.cmd_assemble(self.args), 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest tests.test_build_studyguide.TestAssemble -v`
Expected: FAIL — no attribute `cmd_assemble`.

- [ ] **Step 3: Add the implementation**

Append to `build_studyguide.py`:

```python
import subprocess

VALIDATOR = Path(__file__).resolve().parent / "validate_vault.py"


def _load_plan(plan_json: str):
    plan = json.loads(Path(plan_json).read_text(encoding="utf-8"))
    return plan, plan["meta"], [Path(b["output_path"]) for b in plan["briefs"]]


def cmd_assemble(args) -> int:
    plan, meta, artifacts = _load_plan(args.plan_json)
    existing = [p for p in artifacts if p.exists()]
    issues = check_artifacts(existing)
    if issues:
        print("Artifact contract violations:\n  " + "\n  ".join(issues), file=sys.stderr)
        return 1

    cards = []
    for p in existing:
        cards.extend(parse_flashcards(p.read_text(encoding="utf-8", errors="ignore")))

    sg = Path(meta["studyguide_dir"])
    course, slug = meta["course"], meta["slug"]
    vault_md = sg / f"{course}-{slug}-Flashcards.md"
    vault_md.write_text(
        f"---\ntitle: \"{course} {slug} Flashcards\"\nai_study_aid: true\n---\n\n"
        f"# {course} {slug} — Flashcards\n\n"
        "_One atomic fact per card. `Term :: Definition` doubles as the Obsidian "
        "spaced-repetition format and the Quizlet export source._\n\n"
        + "\n".join(f"{t} :: {d}" for t, d in cards) + "\n", encoding="utf-8")

    n = write_quizlet(cards, sg / f"{course}-{slug}-Flashcards-quizlet.txt")
    print(f"assembled {n} flashcards for {course} {slug}")
    return 0


def cmd_finalize(args) -> int:
    plan, meta, _ = _load_plan(args.plan_json)
    rc = subprocess.run([sys.executable, str(VALIDATOR)]).returncode
    if rc != 0:
        print("validate_vault.py reported issues — NOT writing insight brief; "
              "fix broken links first.", file=sys.stderr)
        return rc

    vault_root = Path(args.vault_root)
    course, slug = meta["course"], meta["slug"]
    inbox = vault_root / "01_CAPTURE" / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    brief = inbox / f"{course}-studyguide-{today}.md"
    brief.write_text(
        f"---\ntitle: \"{course} study guide ready ({slug})\"\n"
        f"ai_study_aid: true\nlast_updated: {today}\n---\n\n"
        f"# {course} {slug} — Study guide ready\n\n"
        f"- Master guide: `StudyGuide-{slug}/`\n"
        f"- Flashcards exportable to Quizlet (tab-delimited .txt)\n"
        f"- Scenario MCQs + IRAC essay practice in-vault (study aid only)\n",
        encoding="utf-8")
    print(f"finalize OK — insight brief at {brief}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Study Guide builder (Layer 4).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--course", required=True)
    p.add_argument("--range", required=True)
    p.add_argument("--vault-dir", required=True, dest="vault_dir")
    p.add_argument("--staging-root", default=str(Path.home() / "canvas_downloads"),
                   dest="staging_root")
    p.add_argument("--exam", default=None)
    p.set_defaults(func=cmd_plan)

    a = sub.add_parser("assemble")
    a.add_argument("--plan-json", required=True, dest="plan_json")
    a.set_defaults(func=cmd_assemble)

    f = sub.add_parser("finalize")
    f.add_argument("--plan-json", required=True, dest="plan_json")
    f.add_argument("--vault-root", required=True, dest="vault_root")
    f.set_defaults(func=cmd_finalize)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full test suite**

Run: `cd ~/cognitive_os_pipeline && python3 -m unittest discover -s tests -v`
Expected: PASS (all tests across Tasks 1–9). Also smoke-test the CLI: `python3 scripts/build_studyguide.py --help` lists `plan|assemble|finalize`.

- [ ] **Step 5: Commit**

```bash
cd ~/cognitive_os_pipeline
git add scripts/build_studyguide.py tests/test_build_studyguide.py
git commit -m "feat(studyguide): assemble + finalize subcommands and CLI dispatch"
```

---

### Task 10: Write the `studyguide` SKILL.md (orchestrator + subagent + condenser prompts)

**Files:**
- Create: `~/.claude/skills/studyguide/SKILL.md`

**Interfaces:**
- Consumes: `build_studyguide.py` subcommands (`plan`, `assemble`, `finalize`), `courses.yaml`, the per-module artifact contract.
- Produces: the human/agent-facing procedure. No unit test (prose skill); verified manually in Task 11.

- [ ] **Step 1: Write the skill file**

Create `~/.claude/skills/studyguide/SKILL.md` with this content:

````markdown
---
name: studyguide
description: Use when the user wants to turn finalized course notes into an exam-prep study guide — flashcards (Quizlet-exportable), scenario practice MCQs, IRAC essay practice, and an optimal spaced study path. Triggers include "/studyguide <COURSE> <range>" (e.g. "/studyguide IRE430 M07-M12", "/studyguide POL302 all"). Layer 4 (study) of the Canvas study pipeline; runs after /coursenotes has produced notes.
---

# studyguide

## Overview

Layer 4 of the Canvas study pipeline. Consumes the **finalized vault notes** for a
section range and produces an evidence-based study guide: an **optimal spaced +
interleaved study path**, **atomic flashcards** (exportable to Quizlet), **scenario
practice MCQs** (with rationales + Bloom tags), and **IRAC essay practice**
(prompts + rubrics — never model essays). Research basis:
`~/cognitive_os_pipeline/docs/research/2026-06-21-study-methods-deep-research.md`.

**Core principles:**
1. **Study aid only.** Every output carries `ai_study_aid: true`. Essay practice is
   scaffolds + rubrics; **never** model/sample essays. (UofT `ai_study_aid` policy.)
2. **Built on the two high-utility techniques** — retrieval practice + spaced
   practice — not rereading. The study path is the spine, not nicer notes.
3. **Notes are the spine; `_text_cache` verifies/deepens.** No re-fetch, no Deepgram.
4. **Zero broken wikilinks** — `validate_vault.py` is the gate (run in `finalize`).

## When to Use

- User runs `/studyguide <COURSE> <range>` — e.g. `/studyguide IRE430 M07-M12`,
  `/studyguide POL302 all`, optionally `--exam YYYY-MM-DD`.
- User asks to make flashcards / practice questions / a study guide / exam prep
  from already-synthesized course notes.

## Prerequisites

- `/coursenotes <course>` has produced module notes in the course `vault_dir`.
- `~/canvas_downloads/courses.yaml` has the course's `vault_dir` + `subject_type`.

## Procedure

### 1. Load config
Read `~/canvas_downloads/courses.yaml` → `vault_root`, the course's `vault_dir`,
`subject_type`. Compute the absolute vault dir = `<vault_root>/<vault_dir>`.

### 2. Plan (deterministic)
```bash
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py plan \
  --course <COURSE> --range <RANGE> \
  --vault-dir "<vault_root>/<vault_dir>" \
  [--exam YYYY-MM-DD]
```
This resolves the section notes, writes `StudyGuide-<slug>/_schedule.md` and
`StudyGuide-<slug>/_plan.json`, and prints the plan. Read `_plan.json`: it lists one
**brief** per module (`module`, `note_path`, `cache_sources`, `output_path`).

### 3. Spawn one subagent per module (parallel)
For each brief, dispatch a general-purpose subagent with the **Subagent Prompt
Template** below, substituting the brief's fields and the course `subject_type`.
Each subagent writes ONE artifact to its `output_path`. Per-module isolation keeps
the i3/8GB box stable and makes failures resumable (re-run only the failed module).

### 4. Assemble (deterministic, after all artifacts exist)
```bash
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py assemble \
  --plan-json "<vault_root>/<vault_dir>/StudyGuide-<slug>/_plan.json"
```
This **gates on the artifact contract** (fails if any artifact is missing a section,
an MCQ rationale/Bloom tag, an essay rubric, the `ai_study_aid` flag, or contains a
banned model-essay marker — fix the offending module's subagent output and re-run),
then writes `<Course>-<slug>-Flashcards.md` (vault) and
`<Course>-<slug>-Flashcards-quizlet.txt` (Quizlet import: tab-delimited, newline per
card → in Quizlet choose "Tab" between term/definition and "New line" between cards).

### 5. Condenser pass (generative)
Dispatch ONE subagent to write the master guide
`<Course>-<slug>-Study-Guide.md` (`ai_study_aid: true`). It must:
- Embed the contents of `StudyGuide-<slug>/_schedule.md` (the spaced study path) verbatim.
- Add a short overview + a mermaid map of how the modules connect.
- Link to each per-module artifact and to `<Course>-<slug>-Flashcards.md` — **only
  after confirming each target file exists** (zero-broken-links rule).
- Link up to `[[<Course>-Reading-Materials]]` and `[[<Course> Syllabus]]` (verify they exist).

### 6. Finalize (deterministic gate + routing)
```bash
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py finalize \
  --plan-json "<vault_root>/<vault_dir>/StudyGuide-<slug>/_plan.json" \
  --vault-root "<vault_root>"
```
Runs `validate_vault.py` (broken-link gate). If clean, writes the insight brief to
`01_CAPTURE/Inbox/`. If the validator reports issues, STOP and fix the links.

### 7. Report
Tell the user: study-guide folder path, flashcard counts + Quizlet file path,
MCQ/essay artifact paths, the schedule (ordinal or dated + crunch flag), validation result.

## Subagent Prompt Template (per module)

```
You are generating ONE module's study-guide artifact for a university course.
STUDY AID ONLY — never produce submittable or model essay prose.

Course: <COURSE> (<subject_type>)
Module: <module tag, e.g. M07>
SPINE (structure + scope) — read this note: <note_path>
VERIFY/DEEPEN against source text (read each; may be empty): <cache_sources>
Write the artifact to: <output_path>

Produce EXACTLY these sections (the deterministic helper parses + validates them):

## Why-It's-True Prompts
- 4–6 elaborative-interrogation prompts ("Why is this true? Why does this rule/▸
  concept apply here and not there?"). Questions only — no answers.

## Flashcards
- Atomic, one fact per line, format `Term :: Definition`. For law: `Case :: holding`,
  `Statute/§ :: rule`; for poli-sci: `Concept/Thinker :: claim`. 10–20 cards.
  No multi-fact lists on one line.

## Practice MCQs
- 5–8 items. Each LEADS WITH A SHORT SCENARIO/VIGNETTE (all context in the stem),
  4 options, distractors = common misconceptions (homogeneous, mutually exclusive).
  No "all/none of the above", no double negatives, no absolutes. After each item give:
  `Answer:` <letter>, a per-distractor `Rationale:` (why each wrong option is wrong),
  and a `Bloom:` tag (Remember/Understand/Apply/Analyze). Put answers/rationales in a
  collapsible `> [!info]- Answer` callout so they're hidden during practice.

## Essay Practice
- 2–3 IRAC practice prompts modeled on this course's exam style. For each: an
  issue-spotting checklist (which facts trigger which rules), an IRAC SKELETON
  (Issue phrasing; Rule + elements; the KINDS of arguments to make in Analysis —
  plaintiff vs defendant, majority vs minority rule, policy; brief Conclusion), and a
  `Rubric:` (points for issues spotted / rule accuracy / depth of analysis &
  counterarguments / organization). OUTLINES + RUBRICS ONLY — never a written model
  answer. Do NOT use the words "Model Answer", "Model Essay", or "Sample Essay".

Frontmatter must include `ai_study_aid: true`. Link ONLY to
[[<COURSE>-Reading-Materials]] and [[<COURSE> Syllabus]] if you link at all; refer to
other modules in plain text. Be faithful to the note + sources; mark any inference.
Report the file path and a 2-line summary when done.
```

## Subject-Type Adaptation

| subject_type | Flashcards emphasize | MCQ/essay emphasis |
|---|---|---|
| law | Cases → holdings, statutes/§ → rules, legal tests | Fact-pattern vignettes; IRAC essays |
| political-science | Thinkers, theses, evidence, debates | Scenario application of theories; argumentative essays |
| social-science / general | Concepts, studies, frameworks | Applied scenarios; structured-argument essays |

## Common Mistakes

- **Treating the guide as nicer notes to reread** — the research is explicit that
  rereading is low-utility. The deliverable is self-testing on a spaced schedule.
- **Emitting unverified wikilinks** → broken-link gate fails. Per-module artifacts
  link only to the reading index/syllabus; the condenser adds cross-links after
  confirming targets exist.
- **Writing model essays** → violates the study-aid boundary AND the `assemble`
  contract check will block the run. Scaffolds + rubrics only.
- **Skipping `assemble`/`finalize` gates** → run them; they enforce the contract and
  the zero-broken-links rule.
- **Quizlet auto-MCQ instead of in-vault MCQs** → Quizlet's generated MCQs are
  shallow (distractor-from-other-cards). Real scenario MCQs are authored in-vault.

## Real-World Impact

Built on the deep-research evidence base (Dunlosky et al. high-utility techniques;
IRAC for law/poli-sci essays; misconception-based MCQ design) and the existing
pipeline's per-module-subagent + validate-gate pattern.
````

- [ ] **Step 2: Verify the skill file parses and paths resolve**

Run:
```bash
test -f ~/.claude/skills/studyguide/SKILL.md && head -5 ~/.claude/skills/studyguide/SKILL.md
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py --help
```
Expected: frontmatter prints; helper shows `plan|assemble|finalize`.

- [ ] **Step 3: Commit**

```bash
cd ~/cognitive_os_pipeline
git add -A
git commit -m "feat(studyguide): orchestrator skill with subagent + condenser prompts"
```

(Note: the skill lives under `~/.claude/skills/`, outside this repo. Commit it separately if that directory is version-controlled; otherwise this commit covers only repo files. Mention the skill path to the user in the final report.)

---

### Task 11: End-to-end smoke test on POL302 + docs/config touch-ups

**Files:**
- Modify: `~/canvas_downloads/courses.yaml` (add a clarifying comment that `/studyguide` is Layer 4)
- Modify: `~/cognitive_os_pipeline/HANDOFF.md` (note the new layer)

**Interfaces:**
- Consumes: the full helper + skill. No new code; this is an integration gate.

- [ ] **Step 1: Run `plan` against the real POL302 vault (read-only-ish; writes only into a new StudyGuide folder)**

Resolve POL302's vault dir from `courses.yaml` (`03_RESOURCES/Academic/POL302`). Pick a 1–2 module range that exists (list the folder first), then:
```bash
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py plan \
  --course POL302 --range all \
  --vault-dir "/Users/thomasroberts/Documents/NEW_COGNITIVE_OS/03_RESOURCES/Academic/POL302"
```
Expected: prints a `_plan.json` with one brief per POL302 module note; `StudyGuide-all/_schedule.md` and `_plan.json` created. Confirm hub files (Syllabus, Reading-Materials, Notes-Package) are NOT in the briefs.

- [ ] **Step 2: Hand-create one tiny artifact and verify the gates end-to-end**

Create a minimal valid artifact at the first brief's `output_path` (copy the structure from the `TestAssemble.setUp` GOOD fixture), then:
```bash
SG="/Users/thomasroberts/Documents/NEW_COGNITIVE_OS/03_RESOURCES/Academic/POL302/StudyGuide-all"
python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py assemble --plan-json "$SG/_plan.json"
```
Expected: writes `POL302-all-Flashcards.md` + `POL302-all-Flashcards-quizlet.txt`. (Because only one artifact exists, the contract check runs on existing artifacts only; full runs require all modules — this smoke test validates the plumbing.)

- [ ] **Step 3: Verify the validator gate is wired**

Run: `python3 ~/cognitive_os_pipeline/scripts/build_studyguide.py finalize --plan-json "$SG/_plan.json" --vault-root "/Users/thomasroberts/Documents/NEW_COGNITIVE_OS"`
Expected: runs `validate_vault.py`; if the vault is clean, writes an insight brief to `01_CAPTURE/Inbox/`. If it reports pre-existing broken links unrelated to this run, note them — they are not introduced by studyguide.

- [ ] **Step 4: Clean up the smoke-test artifacts**

```bash
rm -rf "/Users/thomasroberts/Documents/NEW_COGNITIVE_OS/03_RESOURCES/Academic/POL302/StudyGuide-all"
rm -f "/Users/thomasroberts/Documents/NEW_COGNITIVE_OS/01_CAPTURE/Inbox/POL302-studyguide-"*.md
```
(Remove only the throwaway smoke-test outputs; leave real notes untouched.)

- [ ] **Step 5: Update HANDOFF.md + courses.yaml comment, run full suite, commit**

Add a `LAYER 4 STUDY  /studyguide` line to the pipeline diagram in `HANDOFF.md §1` and a one-line comment in `courses.yaml` noting `/studyguide <COURSE> <range>` is Layer 4. Then:
```bash
cd ~/cognitive_os_pipeline
python3 -m unittest discover -s tests -v   # expect all green
git add -A
git commit -m "docs(studyguide): record Layer 4 in handoff + course registry; smoke-tested on POL302"
```

---

## Self-Review

**1. Spec coverage:**
- §2 Output home / division of labor → Tasks 6, 9 (vault flashcards + Quizlet export), 10 (in-vault MCQs/essays via subagent). ✓
- §2 Generation source (notes spine + cache) → Task 2 (`sources_for_note`), Task 10 subagent prompt. ✓
- §2 Selection UX (`/studyguide <COURSE> <range>`) → Task 1 (`resolve_sections`), Task 8/9 CLI, Task 10 skill. ✓
- §2 Schedule (ordinal default; dates + crunch on `--exam`) → Tasks 3,4,5,8. ✓
- §3 MCQ rules / essay IRAC / atomic flashcards / 3-pass path → Task 10 subagent prompt + Task 7 contract checks + Tasks 3–5 schedule. ✓
- §4 Architecture (plan→subagents→assemble→condenser→finalize) → Tasks 8,9,10. ✓
- §5 File layout → Tasks 8 (folder/_schedule/_plan), 9 (flashcards), 10 (master guide), 9 (insight brief). ✓
- §6 Idempotency (overwrite folder) → `mkdir(exist_ok=True)` + deterministic paths (Task 8). ✓
- §7 Error handling (missing cache → []; subagent failure → per-module isolation; broken links → finalize gate; bad/past exam → crunch; Quizlet escaping) → Tasks 2,3-4,9,4,6. ✓
- §8 Testing (unit + contract + POL302 smoke + compliance) → Tasks 1–9 unit, Task 7 contract, Task 11 smoke. ✓
- §9 Out of scope honored (no HTML app, no SRS state, no syllabus parsing, no fetch). ✓
- §10 open items: skip-via-hash deferred (idempotent overwrite chosen — acceptable for v1); default counts encoded in Task 10 prompt (10–20 cards, 5–8 MCQs, 2–3 essays); vault flashcard format chosen (`Term :: Definition`). ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every code step has full code; every test step has real assertions. ✓

**3. Type consistency:** `resolve_sections`/`module_num`/`sources_for_note`/`read_frontmatter`/`build_schedule`/`place_dates`/`render_schedule_md`/`parse_flashcards`/`write_quizlet`/`check_artifacts`/`range_slug`/`cmd_plan`/`cmd_assemble`/`cmd_finalize`/`main` — names and signatures are consistent across tasks; session dict keys (`ordinal`,`gap_days`,`activities`,`passes`,`date`) match between Tasks 3,4,5; `_plan.json` shape (`meta`,`briefs[].output_path`,`meta.studyguide_dir`,`meta.course`,`meta.slug`) consistent between Tasks 8 and 9. ✓

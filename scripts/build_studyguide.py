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
import subprocess
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


def build_schedule(modules, per_session: int = 3) -> list:
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


CARD_RE = re.compile(r"^\s*(?:[-*]\s+)?(.+?)\s*::\s*(.+?)\s*$")


def parse_flashcards(text: str) -> list:
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


REQUIRED_SECTIONS = ["## Flashcards", "## Practice MCQs",
                     "## Essay Practice", "## Why-It's-True Prompts"]
BANNED_ESSAY = ("model answer", "model essay", "sample essay")


def section_body(text: str, heading: str) -> str:
    """Return lines after a line == heading, up to the next '## ' line, or ''."""
    lines = text.splitlines()
    body, capturing = [], False
    for line in lines:
        if capturing:
            if line.startswith("## "):
                break
            body.append(line)
        elif line == heading:
            capturing = True
    return "\n".join(body)


def check_artifacts(paths) -> list:
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
            mcq_body = section_body(text, "## Practice MCQs").lower()
            if "rationale:" not in mcq_body:
                issues.append(f"{name}: MCQs missing 'Rationale:'")
            if "bloom:" not in mcq_body:
                issues.append(f"{name}: MCQs missing 'Bloom:' tag")
        if "## Essay Practice" in text and \
                "rubric:" not in section_body(text, "## Essay Practice").lower():
            issues.append(f"{name}: essay section missing 'Rubric:'")
        for banned in BANNED_ESSAY:
            if banned in low:
                issues.append(f"{name}: contains banned model-essay marker '{banned}'")
        if read_frontmatter(text).get("ai_study_aid") != "true":
            issues.append(f"{name}: missing 'ai_study_aid: true' in frontmatter")
    return issues


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

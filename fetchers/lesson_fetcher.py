#!/usr/bin/env python3
"""
Lesson fetcher for Cognitive OS pipeline.
Detects completed AI Engineering lessons from the student's git commits in the
course repo, extracts lesson content, writes structured captures to vault inbox.
Idempotent: skips lessons already captured in vault or inbox.
"""

import json
import os
import re
import subprocess
import hashlib
import datetime
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / "cache"
RUN_LOG = CACHE_DIR / "run_log.jsonl"
HEALTH_FILE = CACHE_DIR / "health.json"
STATE_FILE = CACHE_DIR / "lesson_fetch_last_run.json"

VAULT = Path("/Users/thomasroberts/Documents/NEW_COGNITIVE_OS")
VAULT_INBOX = VAULT / "01_CAPTURE/Inbox"
AI_ENG_BASE = VAULT / "03_RESOURCES/AI_Projects/AI-Engineering"
COURSE_REPO = Path.home() / "Documents/ai-engineering-from-scratch"

PHASE_NAMES = {
    "00": "Setup and Tooling",
    "01": "Math Foundations",
    "02": "ML Fundamentals",
    "03": "Deep Learning Core",
    "04": "Computer Vision",
    "05": "NLP Foundations to Advanced",
    "06": "Speech and Audio",
    "07": "Transformers Deep Dive",
    "08": "Generative AI",
    "09": "Reinforcement Learning",
    "10": "LLMs from Scratch",
    "11": "LLM Engineering",
    "12": "Multimodal AI",
    "13": "Tools and Protocols",
    "14": "Agent Engineering",
    "15": "Autonomous Systems",
    "16": "Multi-Agent and Swarms",
    "17": "Infrastructure and Production",
    "18": "Ethics Safety Alignment",
    "19": "Capstone Projects",
}


def _log(date, job, status, detail=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"date": date, "job": job, "status": status}
    if detail:
        entry["detail"] = detail
    with open(RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _update_health(status, lessons_captured):
    try:
        data = {}
        if HEALTH_FILE.exists():
            with open(HEALTH_FILE) as f:
                data = json.load(f)
        data["lesson_fetch"] = {
            "last_run": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status,
            "lessons_captured": lessons_captured,
        }
        with open(HEALTH_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_sha": None, "processed_lessons": []}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_student_email():
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            cwd=COURSE_REPO, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _get_new_commits(last_sha, author_email):
    """Return list of {sha, date, files} for student commits since last_sha."""
    git_args = ["git", "log", "--name-only", "--pretty=format:COMMIT|%H|%ai"]
    if last_sha:
        git_args.append(f"{last_sha}..HEAD")
    else:
        # First run: look back 90 days to catch any existing student work
        git_args.append("--since=90 days ago")
    if author_email:
        git_args.append(f"--author={author_email}")

    result = subprocess.run(
        git_args, cwd=COURSE_REPO, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")

    commits = []
    current = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("COMMIT|"):
            _, sha, date = line.split("|", 2)
            current = {"sha": sha, "date": date[:10], "files": []}
            commits.append(current)
        elif current and line.startswith("phases/"):
            current["files"].append(line)

    return commits


def _extract_lesson_paths(commits):
    """Derive unique lesson entries from commit file paths."""
    seen = set()
    lessons = []
    for commit in commits:
        for fpath in commit["files"]:
            m = re.match(r"phases/(\d{2}-[^/]+)/(\d{2}-[^/]+)/", fpath)
            if not m:
                continue
            phase_dir = m.group(1)
            lesson_dir = m.group(2)
            key = f"{phase_dir}/{lesson_dir}"
            if key in seen:
                continue
            seen.add(key)
            lessons.append({
                "phase_dir": phase_dir,
                "lesson_dir": lesson_dir,
                "phase_num": phase_dir[:2],
                "date": commit["date"],
                "sha": commit["sha"],
            })
    return lessons


def _lesson_already_captured(lesson_slug):
    """Return True if vault or inbox already has a note for this lesson."""
    if AI_ENG_BASE.exists():
        for md in AI_ENG_BASE.rglob("*.md"):
            if lesson_slug in md.stem:
                return True
    if VAULT_INBOX.exists():
        for md in VAULT_INBOX.glob(f"*{lesson_slug}*.md"):
            return True
    return False


def _parse_docs(docs_path):
    """Extract title, objectives, core concept, key artifacts from docs/en.md."""
    try:
        text = docs_path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Title: first # heading
    title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else None

    # Learning objectives: bullet list under ## Learning Objectives
    obj_m = re.search(r"## Learning Objectives\s*\n((?:[-*]\s+.+\n?)+)", text, re.IGNORECASE)
    objectives = []
    if obj_m:
        for line in obj_m.group(1).splitlines():
            line = line.strip().lstrip("-* ").strip()
            if line:
                objectives.append(line)

    # Core concept: first ## section that isn't boilerplate
    skip = {"learning objectives", "prerequisites", "overview", "introduction", "table of contents"}
    section_matches = list(re.finditer(r"^## (.+)$", text, re.MULTILINE))
    core_concept = ""
    for i, sec in enumerate(section_matches):
        if sec.group(1).strip().lower() in skip:
            continue
        start = sec.end()
        end = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(text)
        body = text[start:end]
        # Strip sub-headers and leading whitespace
        body = re.sub(r"^#{2,}.*$", "", body, flags=re.MULTILINE)
        body = body.strip()
        # Take first 4 sentences of clean prose (skip empty or very short lines)
        sentences = re.split(r"(?<=[.!?])\s+", body)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 25 and not s.strip().startswith("#")]
        core_concept = " ".join(sentences[:4])
        if core_concept:
            break

    # Key artifacts: LaTeX math blocks and code blocks, max 3 total
    artifacts = []
    for m in re.finditer(r"\$\$\n(.+?)\$\$", text, re.DOTALL):
        if len(artifacts) >= 3:
            break
        artifacts.append(f"$$\n{m.group(1).strip()}\n$$")
    for m in re.finditer(r"```(\w*)\n(.+?)```", text, re.DOTALL):
        if len(artifacts) >= 3:
            break
        lang = m.group(1) or "python"
        code = m.group(2).strip()
        # Skip very long blocks
        if len(code) <= 400:
            artifacts.append(f"```{lang}\n{code}\n```")

    return {
        "title": title,
        "objectives": objectives[:6],
        "core_concept": core_concept[:800],
        "artifacts": artifacts,
    }


def _parse_quiz(quiz_path):
    """Return up to 3 post-stage questions from quiz.json."""
    try:
        data = json.loads(quiz_path.read_text(encoding="utf-8"))
        questions = data.get("questions", [])
        return [q for q in questions if q.get("stage") == "post"][:3]
    except Exception:
        return []


def _write_capture(lesson, docs_data, quiz_questions):
    phase_num = lesson["phase_num"]
    phase_name = PHASE_NAMES.get(phase_num, f"Phase {phase_num}")
    lesson_slug = lesson["lesson_dir"]
    title = (docs_data or {}).get("title") or lesson_slug.replace("-", " ").title()

    lines = [
        "---",
        "source: ai-engineering-course",
        f"phase: {phase_num}",
        f"phase_name: {phase_name}",
        f"lesson: {lesson_slug}",
        f"lesson_title: {title}",
        f"completed: {lesson['date']}",
        "tags: [ai-engineering/capture]",
        "---",
        "",
        f"# AI Engineering Capture — {title}",
        f"**Phase {phase_num} | {phase_name}**",
        "",
    ]

    if docs_data and docs_data.get("core_concept"):
        lines += ["## Core Concept", "", docs_data["core_concept"], ""]

    if docs_data and docs_data.get("objectives"):
        lines += ["## Learning Objectives", ""]
        for obj in docs_data["objectives"]:
            lines.append(f"- {obj}")
        lines.append("")

    if docs_data and docs_data.get("artifacts"):
        lines += ["## Key Artifacts", ""]
        for artifact in docs_data["artifacts"]:
            lines.append(artifact)
            lines.append("")

    if quiz_questions:
        lines += ["## Self-Quiz (review later)", ""]
        for q in quiz_questions:
            lines.append(f"- {q['question']}")
        lines.append("")

    fname = f"ai-eng-{lesson['date']}-{lesson_slug}.md"
    out_path = VAULT_INBOX / fname
    VAULT_INBOX.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    today = datetime.date.today().isoformat()

    if not COURSE_REPO.exists():
        _log(today, "lesson_fetch", "skipped", "repo not found")
        _update_health("skipped", 0)
        print("lesson_fetch: repo not found — skipping")
        return

    state = _load_state()
    author_email = _get_student_email()

    try:
        commits = _get_new_commits(state.get("last_sha"), author_email)

        if not commits:
            _log(today, "lesson_fetch", "skipped", "no new student commits")
            _update_health("skipped", 0)
            print("lesson_fetch: no new student commits")
            return

        lessons = _extract_lesson_paths(commits)

        if not lessons:
            _log(today, "lesson_fetch", "skipped", "commits found but no lesson paths")
            _update_health("skipped", 0)
            print("lesson_fetch: no lesson paths in commits")
            _save_state({**state, "last_sha": commits[0]["sha"]})
            return

        captured = 0
        skipped_dup = 0
        for lesson in lessons:
            if _lesson_already_captured(lesson["lesson_dir"]):
                skipped_dup += 1
                continue
            docs_path = COURSE_REPO / "phases" / lesson["phase_dir"] / lesson["lesson_dir"] / "docs" / "en.md"
            quiz_path = COURSE_REPO / "phases" / lesson["phase_dir"] / lesson["lesson_dir"] / "quiz.json"
            docs_data = _parse_docs(docs_path) if docs_path.exists() else None
            quiz_questions = _parse_quiz(quiz_path) if quiz_path.exists() else []
            capture_file = _write_capture(lesson, docs_data, quiz_questions)
            captured += 1
            print(f"lesson_fetch: captured {lesson['lesson_dir']} → {capture_file.name}")

        # Advance state to the newest processed SHA
        state["last_sha"] = commits[0]["sha"]
        if "processed_lessons" not in state:
            state["processed_lessons"] = []
        state["processed_lessons"].extend(l["lesson_dir"] for l in lessons)
        _save_state(state)

        detail = f"lessons_captured={captured}, already_done={skipped_dup}"
        _log(today, "lesson_fetch", "success", detail)
        _update_health("success", captured)
        print(f"lesson_fetch: done — {detail}")

    except Exception as e:
        _log(today, "lesson_fetch", f"error: {e}")
        _update_health("error", 0)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

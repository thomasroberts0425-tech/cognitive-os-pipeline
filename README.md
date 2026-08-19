# Cognitive OS Pipeline

Turns University of Toronto **Quercus (Canvas)** course materials — readings, slides, lecture audio/video, announcements — into formatted **study notes** in an Obsidian vault, and glues together a personal daily/weekly knowledge-management loop (Gmail, Calendar, Drive).

> Study aids only. Per [UofT's AI-for-learning policy](https://studentlife.utoronto.ca/task/using-ai-tools-for-learning-at-u-of-t/), generated notes are tagged `ai_study_aid: true` and are pointers for review — never submittable prose.

## What it does

```
LAYER 1  INGEST    Chrome session (Chrome MCP) hits the Quercus JSON API → downloads files
LAYER 2  SYNTH     One subagent per module → visual study notes (mermaid + tables)
LAYER 3  ROUTE     Insight brief → Obsidian Inbox → evening routine
LAYER 4  STUDY     Flashcards + scenario MCQs + IRAC essay practice + spaced study path
STORAGE            extract_text.py caches text (PDF / OCR / office / Deepgram audio); prune deletes raws, keeps cache
ORCHESTRATION      canvas-sync = fetch → synthesize → validate → prune → report (idempotent)
```

## Layout

| Path | Purpose |
|---|---|
| `fetchers/` | Google API fetchers — `gmail_fetch.py`, `calendar_fetch.py`, `drive_fetch.py`, `lesson_fetcher.py` |
| `scripts/extract_text.py` | Text extractor / router (PDF, OCR, office, audio) |
| `scripts/transcribe.py` | Deepgram audio transcriber |
| `scripts/build_studyguide.py` | Flashcards + MCQs + essay practice generator |
| `scripts/validate_vault.py` | Vault validator — broken-wikilink gate |
| `scripts/check_auth.py` | OAuth token health checker (Gmail / Calendar / Drive) |
| `scripts/*_routine.sh` | Morning / evening / weekly / canvas-sync orchestration |
| `docs/` | Design specs, plans, and research notes |
| `tests/` | Unit tests (`test_build_studyguide.py`) |

## Setup

```bash
pip install -r requirements.txt
```

Secrets and personal data live **outside** version control (see `.gitignore`):

- `auth/` — OAuth `token.json` and API keys (e.g. `deepgram.key`, `chmod 600`)
- `.env` — local environment
- `cache/` — local runtime data

Run `python3 scripts/check_auth.py` to validate the three Google API tokens.

## Constraints & design notes

- **Browser-only Canvas fetch, no API token** — uses the logged-in Chrome session via Chrome MCP; fetch is app-bound (a headless `claude -p` cannot fetch). Background jobs only do headless synthesis + prune of already-staged material.
- **Zero broken wikilinks** — `validate_vault.py` gates output; only emit `[[links]]` to files that exist.
- **Cache then prune** — each file's text is cached at fetch; raws are deleted once cached; notes regenerate from cache without re-fetching.
- **Cloud transcription** — local transcription is impractical on the target hardware (2020 Intel MacBook Air), so audio goes to Deepgram.

See [`HANDOFF.md`](HANDOFF.md) for full operational context, gotchas, and run history.

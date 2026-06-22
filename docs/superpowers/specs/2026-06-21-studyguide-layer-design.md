# Study Guide Layer (Layer 4) — Design Spec

> Validated design for a new layer of the Canvas Study Pipeline that turns finalized course notes into an exam-prep study guide: spaced study path + flashcards (Quizlet-exportable) + scenario MCQs + IRAC essay practice. Date: 2026-06-21. Evidence base: `docs/research/2026-06-21-study-methods-deep-research.md`.

## 1. Purpose & Context

The pipeline currently does INGEST (`/canvas-fetch`) → SYNTHESIZE (`/coursenotes`) → ROUTE (insight brief). This adds **Layer 4: STUDY** — `/studyguide` — which consumes the *finalized vault notes* (the Layer 2 output) for a specified section range and produces exam-ready study artifacts grounded in evidence-based learning science.

It is a study aid, never submittable prose (UofT `ai_study_aid` constraint). All outputs carry `ai_study_aid: true`. Essay practice ships scaffolds and rubrics only — never model essays.

## 2. Locked Decisions

| Decision | Choice |
|---|---|
| Output home | **Obsidian vault** (markdown) + a **Quizlet-importable flashcard file** |
| Division of labor | Study guide, scenario MCQs, IRAC essay practice → **in-vault**; recall flashcards → **vault + Quizlet export**. Long/in-depth questions live in Obsidian. |
| Generation source | **Notes as spine, `_text_cache` (transcripts/PDFs) for verification + depth** |
| Selection UX | **Slash command with args**: `/studyguide <COURSE> <section-range>` (e.g. `/studyguide IRE430 M07-M12`, `/studyguide POL302 all`) |
| Schedule output | **Ordinal spaced plan by default; calendar dates + crunch variant when `--exam <YYYY-MM-DD>` is passed** |
| Implementation | **Approach A**: `/studyguide` skill + `build_studyguide.py` helper + one Claude subagent per module + condenser pass (mirrors `coursenotes`) |

## 3. Evidence-Driven Requirements (from deep research)

The guide is built around the only two "high utility" techniques — **retrieval practice** and **spaced practice** — not rereading. Specifically:

- **Optimal study path = 3 passes, spaced & interleaved.** (1) *Understand* pass: elaborative-interrogation "why is this true / why this rule here?" prompts. (2) *Retrieve* pass: closed-book flashcards + scenario MCQs. (3) *Apply* pass: timed IRAC essay practice + rubric self-marking. Sessions use **expanding intervals** and **interleave older + newer modules** and question types.
- **MCQs:** one objective per item; application/analysis items **lead with a scenario/vignette** (all context in stem); **distractors = common misconceptions**, homogeneous & mutually exclusive; no "all/none of the above," no double negatives, no absolutes; each item tagged with a **Bloom level** and an **answer key + per-distractor rationale**.
- **Essays (IRAC):** per topic — practice prompt(s), an **issue-spotting checklist** (which facts trigger which rules), an **IRAC skeleton** (Issue phrasing; Rule + elements; the *kinds of arguments* for Analysis — plaintiff/defendant, majority/minority rule, policy; brief Conclusion), and a **marking rubric** (issues spotted / rule accuracy / depth of analysis & counterarguments / organization). **Outlines & rubrics only — never model essays.** Analysis carries the most marks.
- **Flashcards:** **atomic, one fact per card** — `Case → holding`, `Statute/§ → rule`, `Concept → definition`, plus a few `Scenario → which rule applies`. Understand-before-memorize.

## 4. Architecture (Approach A)

```
/studyguide <COURSE> <range> [--exam YYYY-MM-DD]
        │
        ▼
build_studyguide.py  (deterministic)
  • resolve range → list of note files in vault + matching _text_cache sources
  • emit a per-module "generation brief" (note path + cache paths) for each module
  • after generation: assemble Quizlet export, render schedule (ordinal + dated/crunch),
    run validate_vault.py gate, write insight brief to 01_CAPTURE/Inbox/
        │
        ▼
N module subagents (1 per module, isolated context)  (generative)
  • read note (spine) + cache (verify/deepen)
  • produce: flashcards (atomic), scenario MCQs (+rationale,+Bloom), IRAC essay block, "why" prompts
  • write per-module study artifact into the study-guide folder
        │
        ▼
condenser pass  (generative, 1 call)
  • assemble master Study Guide (overview + the 3-pass spaced/interleaved schedule)
  • add cross-links across modules; finalize Quizlet export
```

Boundaries: the Python helper owns *everything deterministic and filesystem* (selection, paths, schedule math, export formatting, validation, routing); subagents own *only content generation* for one module each; the condenser owns *cross-module assembly and the schedule narrative*. Each unit is independently testable.

## 5. Outputs & File Layout

Under `~/Documents/NEW_COGNITIVE_OS/03_RESOURCES/Academic/<COURSE>/StudyGuide-<slug>/` (where `<slug>` = the range, e.g. `M07-M12` or `all`):

- `<COURSE>-<slug>-M<NN>.md` — **one combined artifact per module**, each containing all four sections: `## Why-It's-True Prompts`, `## Flashcards` (atomic `Term :: Definition`), `## Practice MCQs` (scenario stems, Bloom-tagged, answer key + per-distractor rationale in a collapsible callout), and `## Essay Practice` (IRAC prompts + issue-spotting checklist + skeleton + rubric). `ai_study_aid: true`. (MCQs and essay practice live *inside* these per-module files — not separate per-range files — so generation stays per-module-isolated; the assemble gate validates each.)
- `<COURSE>-<slug>-Flashcards.md` — assembled atomic cards in vault (`Term :: Definition`, also the Obsidian spaced-repetition inline format), parsed from the per-module artifacts.
- `<COURSE>-<slug>-Flashcards-quizlet.txt` — plain-text Quizlet import (tab between term/definition, newline between cards).
- `<COURSE>-<slug>-Study-Guide.md` — master (condenser): overview, the 3-pass spaced/interleaved schedule (embeds `_schedule.md`; ordinal, plus dated + crunch if `--exam`), and verified links to every part. `ai_study_aid: true`.
- `_schedule.md`, `_plan.json` — internal handoff artifacts (schedule markdown; per-module briefs consumed by `assemble`/`finalize`).
- Insight brief → `~/Documents/NEW_COGNITIVE_OS/01_CAPTURE/Inbox/`.

Wikilinks restricted to files known to exist (per the zero-broken-links constraint); validator is the gate.

## 6. Data Flow & Idempotency

- Reads notes from the vault + text from `_text_cache` (durable copy; survives prune).
- Re-running for the same range is **idempotent**: regenerate overwrites the StudyGuide-<range> folder (or skips unchanged modules via a content hash — to be decided in planning).
- No re-fetch and no Deepgram calls — operates purely on already-synthesized notes + cache.

## 7. Error Handling

- **Empty/garbled cache or missing note** for a module → subagent flags it; helper records a gap in the master guide rather than failing the whole run.
- **Subagent failure / OOM** (i3/8GB risk) → per-module isolation means a failed module is retried/skipped without losing completed modules (idempotent resume).
- **Broken wikilinks** → `validate_vault.py` gate blocks finalization; condenser only links to verified-existing files.
- **`--exam` in the past or too soon** → fall back to the compressed "crunch" schedule with a warning.
- **Quizlet export** → escape/strip the chosen separators from card text to avoid malformed rows.

## 8. Testing

- Helper unit tests: range resolution (`M07-M12`, `all`), schedule math (ordinal + dated back-planning + crunch), Quizlet export formatting/escaping, validator invocation.
- Generation contract tests: subagent output conforms to required sections (flashcards atomic, MCQs have rationale + Bloom tag, essay block has rubric, no model-essay prose).
- Integration smoke test on **POL302** (the done reference course) for a small range; confirm validator passes and Quizlet file imports.
- Compliance check: every output file carries `ai_study_aid: true`; essay files contain no full model answers.

## 9. Out of Scope (YAGNI)

- No interactive HTML quiz app (Quizlet provides the drilling UI).
- No spaced-repetition state tracking inside the vault (Quizlet/Anki own that).
- No syllabus parsing / assessment-weight calibration in v1 (exam format inferred from notes + research; revisit later).
- No new fetching or transcription.

## 10. Open Items for Planning

- Per-module skip-via-hash vs full overwrite on re-run.
- Default counts per module (flashcards / MCQs / essay prompts) before it becomes noise.
- Exact vault flashcard format (which SRS plugin convention to target).

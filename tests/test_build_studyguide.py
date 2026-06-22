# tests/test_build_studyguide.py
import json, sys, unittest
from datetime import date
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


if __name__ == "__main__":
    unittest.main()

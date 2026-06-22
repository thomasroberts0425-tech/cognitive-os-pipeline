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


if __name__ == "__main__":
    unittest.main()

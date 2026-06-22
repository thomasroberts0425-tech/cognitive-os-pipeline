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

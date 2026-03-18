import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLD_PROJECT_ROOT = str(Path.home() / "Project_WebSocket" / "ClawWork")
DOC_SUFFIXES = {".md", ".txt"}
EXCLUDED_PARTS = {".git", "__pycache__", "node_modules", "dist", "logs", "data", "postmortem"}


class ExternalProjectPathDocsTests(unittest.TestCase):
    def test_docs_have_no_old_project_root_references(self):
        offenders = []
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
                continue
            if EXCLUDED_PARTS & set(path.parts):
                continue
            text = path.read_text(errors="ignore")
            if OLD_PROJECT_ROOT in text:
                offenders.append(str(path))

        self.assertEqual(offenders, [], f"Found old project-root references in docs: {offenders}")


if __name__ == "__main__":
    unittest.main()

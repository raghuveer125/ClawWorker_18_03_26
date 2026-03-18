import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLD_PROJECT_ROOT = str(Path.home() / "Project_WebSocket" / "ClawWork")
CODE_GLOBS = ("*.py", "*.sh", "*.js", "*.jsx", "*.ts", "*.tsx")


class ExternalProjectPathTests(unittest.TestCase):
    def test_runtime_code_has_no_old_project_root_references(self):
        offenders = []
        for pattern in CODE_GLOBS:
            for path in PROJECT_ROOT.rglob(pattern):
                parts = set(path.parts)
                if ".git" in parts or "__pycache__" in parts or "node_modules" in parts or "dist" in parts:
                    continue
                if path == Path(__file__).resolve():
                    continue
                text = path.read_text(errors="ignore")
                if OLD_PROJECT_ROOT in text:
                    offenders.append(str(path))

        self.assertEqual(offenders, [], f"Found old project-root references: {offenders}")


if __name__ == "__main__":
    unittest.main()

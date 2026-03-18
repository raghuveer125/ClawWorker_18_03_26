import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from api import server


class ArtifactEndpointTests(unittest.TestCase):
    def test_recent_artifacts_are_sorted_by_real_file_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir)
            older = data_path / "agent-old" / "sandbox" / "2999-11-03" / "old.pdf"
            newer = data_path / "agent-new" / "sandbox" / "2026-03-18" / "new.xlsx"
            skipped = data_path / "agent-skip" / "sandbox" / "2026-03-18" / "code_exec" / "skip.pdf"

            older.parent.mkdir(parents=True, exist_ok=True)
            newer.parent.mkdir(parents=True, exist_ok=True)
            skipped.parent.mkdir(parents=True, exist_ok=True)

            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            skipped.write_bytes(b"skip")

            os.utime(older, (1_000, 1_000))
            os.utime(newer, (2_000, 2_000))
            os.utime(skipped, (3_000, 3_000))

            with patch.object(server, "DATA_PATH", data_path):
                result = asyncio.run(server.get_artifacts(count=10, sort="recent"))

        self.assertEqual(result["sort"], "recent")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            [artifact["filename"] for artifact in result["artifacts"]],
            ["new.xlsx", "old.pdf"],
        )
        self.assertEqual(result["artifacts"][0]["work_date"], "2026-03-18")
        self.assertEqual(result["artifacts"][1]["work_date"], "2999-11-03")
        self.assertTrue(result["artifacts"][1]["work_date_is_future"])
        self.assertIn("modified_at", result["artifacts"][0])
        self.assertIn("timestamp", result)

    def test_invalid_artifact_sort_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.get_artifacts(count=10, sort="oldest"))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("recent", str(ctx.exception.detail))

    def test_random_artifacts_endpoint_remains_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir)
            artifact = data_path / "agent-random" / "sandbox" / "2026-03-18" / "sample.docx"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"sample")

            with patch.object(server, "DATA_PATH", data_path):
                result = asyncio.run(server.get_random_artifacts(count=5))

        self.assertEqual(result["sort"], "random")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["artifacts"][0]["filename"], "sample.docx")


if __name__ == "__main__":
    unittest.main()

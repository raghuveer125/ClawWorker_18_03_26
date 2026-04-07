import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


LIVEBENCH_ROOT = Path(__file__).resolve().parents[1]
if str(LIVEBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVEBENCH_ROOT))

from api import server


class AgentVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.tmpdir.name)

        self._seed_real_worker("real-worker")
        self._seed_learning_worker("learning-worker")
        self._seed_seed_only_stub("stock-agent")
        self._seed_seed_only_stub("gpt-4o-test")
        self._seed_effectiveness_only_stub("test-effectiveness-agent")

        self.data_path_patch = patch.object(server, "DATA_PATH", self.data_path)
        self.data_path_patch.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.data_path_patch.stop()
        self.tmpdir.cleanup()

    def _agent_dir(self, signature: str) -> Path:
        return self.data_path / signature

    def _write_jsonl(self, path: Path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{json.dumps(row)}\n" for row in rows),
            encoding="utf-8",
        )

    def _write_json(self, path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _seed_balance_history(self, signature: str):
        self._write_jsonl(
            self._agent_dir(signature) / "economic" / "balance.jsonl",
            [
                {
                    "date": "initialization",
                    "balance": 1000.0,
                    "net_worth": 1000.0,
                    "survival_status": "stable",
                    "total_token_cost": 0.0,
                    "total_work_income": 0.0,
                },
                {
                    "date": "2026-03-19",
                    "balance": 1025.0,
                    "net_worth": 1025.0,
                    "survival_status": "stable",
                    "total_token_cost": 2.5,
                    "total_work_income": 30.0,
                },
            ],
        )

    def _seed_real_worker(self, signature: str):
        self._seed_balance_history(signature)
        self._write_jsonl(
            self._agent_dir(signature) / "work" / "tasks.jsonl",
            [
                {
                    "task_id": "task-001",
                    "date": "2026-03-19",
                    "title": "Run a stock scan",
                }
            ],
        )
        terminal_log = self._agent_dir(signature) / "terminal_logs" / "2026-03-19.log"
        terminal_log.parent.mkdir(parents=True, exist_ok=True)
        terminal_log.write_text("worker online\n", encoding="utf-8")

    def _seed_learning_worker(self, signature: str):
        self._seed_balance_history(signature)
        self._write_jsonl(
            self._agent_dir(signature) / "memory" / "memory.jsonl",
            [
                {
                    "topic": "Signal calibration",
                    "timestamp": "2026-03-19T09:15:00",
                    "date": "2026-03-19",
                    "knowledge": "Remember the last watchlist rebalance.",
                }
            ],
        )

    def _seed_seed_only_stub(self, signature: str):
        self._seed_balance_history(signature)

    def _seed_effectiveness_only_stub(self, signature: str):
        self._seed_balance_history(signature)
        self._write_json(
            self._agent_dir(signature) / "knowledge_effectiveness" / "knowledge_index.json",
            {
                "topic-a": {
                    "total_uses": 1,
                    "successful_uses": 1,
                    "total_earnings": 15.0,
                    "last_used": "2026-03-19",
                }
            },
        )
        self._write_jsonl(
            self._agent_dir(signature) / "knowledge_effectiveness" / "usage.jsonl",
            [
                {
                    "topic": "topic-a",
                    "date": "2026-03-19",
                    "earnings": 15.0,
                }
            ],
        )

    def test_real_agent_classifier_requires_runtime_evidence(self):
        self.assertTrue(server._is_real_agent_dir(self._agent_dir("real-worker")))
        self.assertTrue(server._is_real_agent_dir(self._agent_dir("learning-worker")))
        self.assertFalse(server._is_real_agent_dir(self._agent_dir("stock-agent")))
        self.assertFalse(server._is_real_agent_dir(self._agent_dir("gpt-4o-test")))
        self.assertFalse(server._is_real_agent_dir(self._agent_dir("test-effectiveness-agent")))

    def test_agents_endpoint_excludes_stub_agents(self):
        response = self.client.get("/api/agents")

        self.assertEqual(response.status_code, 200)
        signatures = {agent["signature"] for agent in response.json()["agents"]}
        self.assertEqual(signatures, {"real-worker", "learning-worker"})

    def test_leaderboard_endpoint_excludes_stub_agents(self):
        response = self.client.get("/api/leaderboard")

        self.assertEqual(response.status_code, 200)
        signatures = {agent["signature"] for agent in response.json()["agents"]}
        self.assertEqual(signatures, {"real-worker", "learning-worker"})

    def test_stub_agent_endpoints_return_404(self):
        stub_paths = [
            "/api/agents/stock-agent",
            "/api/agents/stock-agent/tasks",
            "/api/agents/stock-agent/economic",
            "/api/agents/stock-agent/learning",
            "/api/agents/stock-agent/learning/roi",
            "/api/agents/stock-agent/institutional-shadow/latest",
            "/api/agents/stock-agent/dashboard-supplemental",
            "/api/agents/test-effectiveness-agent",
        ]

        for path in stub_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"], "Agent not found")

    def test_real_agent_endpoints_remain_available(self):
        checks = {
            "/api/agents/real-worker": 200,
            "/api/agents/real-worker/tasks": 200,
            "/api/agents/real-worker/economic": 200,
            "/api/agents/real-worker/learning": 200,
            "/api/agents/learning-worker": 200,
            "/api/agents/learning-worker/learning": 200,
        }

        for path, expected_status in checks.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, expected_status)

        learning_payload = self.client.get("/api/agents/learning-worker/learning").json()
        self.assertEqual(len(learning_payload["entries"]), 1)
        self.assertEqual(learning_payload["entries"][0]["topic"], "Signal calibration")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from contracts import Phase4Input
from decision_engine import Phase4DecisionEngine


def _load_input(path: Path) -> Phase4Input:
    return Phase4Input(**json.loads(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 4 advanced feature decision engine")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

    item = _load_input(Path(args.input))
    engine = Phase4DecisionEngine()
    decision = engine.evaluate(item)

    print(json.dumps({"input": asdict(item), "decision": asdict(decision)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

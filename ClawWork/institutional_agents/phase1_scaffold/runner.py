import argparse
import json
from dataclasses import asdict
from pathlib import Path

from config import SignalConfig
from models import MarketInput
from signal_engine import SignalEngine


def _load_input(input_path: Path) -> list[MarketInput]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    return [MarketInput(**row) for row in raw]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 signal engine in paper mode")
    parser.add_argument("--input", required=True, help="Path to input JSON file")
    args = parser.parse_args()

    inputs = _load_input(Path(args.input))
    engine = SignalEngine(SignalConfig())

    outputs = [asdict(engine.decide(item)) for item in inputs]
    print(json.dumps(outputs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

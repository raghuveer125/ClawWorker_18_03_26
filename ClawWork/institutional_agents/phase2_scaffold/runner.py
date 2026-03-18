from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from contracts import MomentumSignal, OptionChainInput, OptionRow
from decision_layer import DecisionLayer
from options_analyst import OptionsAnalyst


def _load_input(path: Path) -> OptionChainInput:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = [OptionRow(**item) for item in raw.get("rows", [])]
    return OptionChainInput(
        underlying=raw["underlying"],
        underlying_change_pct=raw["underlying_change_pct"],
        iv_percentile=raw["iv_percentile"],
        straddle_breakout_direction=raw["straddle_breakout_direction"],
        straddle_band_pct=float(raw.get("straddle_band_pct", 12.0)),
        rows=rows,
    )


def _build_momentum_signal(underlying_change_pct: float) -> MomentumSignal:
    if underlying_change_pct >= 0.4:
        return MomentumSignal(action="BUY_CALL", confidence="MEDIUM")
    if underlying_change_pct <= -0.4:
        return MomentumSignal(action="BUY_PUT", confidence="MEDIUM")
    return MomentumSignal(action="NO_TRADE", confidence="LOW")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 2 options scaffold")
    parser.add_argument("--input", required=True, help="Path to option-chain json input")
    args = parser.parse_args()

    chain_input = _load_input(Path(args.input))
    analyst = OptionsAnalyst()
    decision_layer = DecisionLayer()

    momentum = _build_momentum_signal(chain_input.underlying_change_pct)
    options_signal = analyst.analyze(chain_input)
    final = decision_layer.merge(momentum, options_signal)

    payload = {
        "momentum_signal": asdict(momentum),
        "options_signal": asdict(options_signal),
        "final_decision": asdict(final),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Context integrity validation for the scalping engine.
"""

from dataclasses import dataclass
from typing import Any, Dict, List

from .base import BotContext


@dataclass
class ValidationIssue:
    phase: str
    field: str
    message: str
    critical: bool = False


class ContextIntegrityError(RuntimeError):
    """Raised when the shared bot context is malformed in a critical way."""

    def __init__(self, phase: str, issues: List[ValidationIssue]):
        self.phase = phase
        self.issues = issues
        super().__init__(summarize_issues(issues))


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def validate_spot_data(context: BotContext, phase: str = "analysis") -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    spot_data = context.data.get("spot_data")
    if spot_data is None:
        issues.append(ValidationIssue(phase, "spot_data", "spot_data missing", critical=False))
        return issues
    if not _is_mapping(spot_data):
        issues.append(ValidationIssue(phase, "spot_data", "spot_data must be a dict", critical=True))
        return issues
    if not spot_data:
        issues.append(ValidationIssue(phase, "spot_data", "spot_data empty", critical=False))
        return issues

    for symbol, spot in spot_data.items():
        for attr in ("ltp", "open", "high", "low"):
            if getattr(spot, attr, None) is None and not (_is_mapping(spot) and spot.get(attr) is not None):
                issues.append(
                    ValidationIssue(
                        phase,
                        f"spot_data[{symbol}].{attr}",
                        f"missing {attr} for {symbol}",
                        critical=True,
                    )
                )
                break
    return issues


def validate_option_data(context: BotContext, phase: str = "analysis") -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    option_chains = context.data.get("option_chains")
    if option_chains is None:
        issues.append(ValidationIssue(phase, "option_chains", "option_chains missing", critical=False))
        return issues
    if not _is_mapping(option_chains):
        issues.append(ValidationIssue(phase, "option_chains", "option_chains must be a dict", critical=True))
        return issues
    if not option_chains:
        issues.append(ValidationIssue(phase, "option_chains", "option_chains empty", critical=False))
        return issues

    for symbol, chain in option_chains.items():
        options = getattr(chain, "options", None)
        if options is None and _is_mapping(chain):
            options = chain.get("options")
        if not isinstance(options, list) or not options:
            issues.append(
                ValidationIssue(
                    phase,
                    f"option_chains[{symbol}].options",
                    f"no option rows for {symbol}",
                    critical=False,
                )
            )
    return issues


def validate_futures_data(context: BotContext, phase: str = "analysis") -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    futures_data = context.data.get("futures_data")
    if futures_data is None:
        issues.append(ValidationIssue(phase, "futures_data", "futures_data missing", critical=False))
        return issues
    if not _is_mapping(futures_data):
        issues.append(ValidationIssue(phase, "futures_data", "futures_data must be a dict", critical=True))
        return issues
    if not futures_data:
        issues.append(ValidationIssue(phase, "futures_data", "futures_data empty", critical=False))
        return issues

    for symbol, fut in futures_data.items():
        if getattr(fut, "ltp", None) is None and not (_is_mapping(fut) and fut.get("ltp") is not None):
            issues.append(
                ValidationIssue(
                    phase,
                    f"futures_data[{symbol}].ltp",
                    f"missing futures ltp for {symbol}",
                    critical=True,
                )
            )
    return issues


def summarize_issues(issues: List[ValidationIssue]) -> str:
    return "; ".join(f"{issue.field}: {issue.message}" for issue in issues)


def validate_phase_inputs(phase: str, context: BotContext) -> List[ValidationIssue]:
    phase = phase.lower()
    issues: List[ValidationIssue] = []

    if phase in {"analysis", "risk", "execution"}:
        issues.extend(validate_spot_data(context, phase))
        issues.extend(validate_option_data(context, phase))

    if phase in {"analysis", "risk"}:
        issues.extend(validate_futures_data(context, phase))

    if phase == "execution":
        positions = context.data.get("positions", [])
        if positions is not None and not isinstance(positions, list):
            issues.append(ValidationIssue(phase, "positions", "positions must be a list", critical=True))

    return issues


def raise_for_critical_issues(phase: str, context: BotContext) -> List[ValidationIssue]:
    issues = validate_phase_inputs(phase, context)
    critical = [issue for issue in issues if issue.critical]
    if critical:
        raise ContextIntegrityError(phase, critical)
    return issues

"""Compatibility wrapper around the shared FYERS client."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_SHARED_ROOT = Path(__file__).resolve().parents[3]
if str(_SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(_SHARED_ROOT))

from shared_project_engine.auth import FyersClient as SharedFyersClient
from data_platform.market_consumer import KafkaMarketDataClient, build_kafka_market_client


class FyersClient(SharedFyersClient):
    """Backwards-compatible alias for the shared FYERS client."""


class MarketDataClient(KafkaMarketDataClient):
    """Market data client — Kafka-backed with HTTP fallback."""


def resolve_market_env_file(explicit_env_file: Optional[str] = None) -> Optional[str]:
    if explicit_env_file:
        candidate = Path(explicit_env_file).expanduser()
        return str(candidate) if candidate.exists() else explicit_env_file

    candidate_paths = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[3] / ".env",
    ]
    seen = set()
    for candidate in candidate_paths:
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return str(resolved)
    return None


def _env_file_has_value(env_file: Optional[str], key: str) -> bool:
    if os.getenv(key):
        return True
    if not env_file:
        return False

    env_path = Path(env_file)
    if not env_path.exists():
        return False

    try:
        lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False

    prefix = f"{key}="
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        return bool(line.split("=", 1)[1].strip())
    return False


def market_data_client_kwargs(explicit_env_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Build resilient MarketDataClient kwargs for LiveBench flows.

    The API server and screener often run from shells that do not load the
    workspace-root `.env` into `os.environ`. Resolve that file explicitly and
    allow local FYERS fallback whenever a token is configured there.
    """
    env_file = resolve_market_env_file(explicit_env_file)
    has_local_credentials = _env_file_has_value(env_file, "FYERS_ACCESS_TOKEN")
    return {
        "env_file": env_file,
        "fallback_to_local": has_local_credentials,
        "strict_mode": False,
    }


def build_market_data_client(explicit_env_file: Optional[str] = None, **overrides: Any) -> MarketDataClient:
    client = build_kafka_market_client()
    return client  # type: ignore[return-value]

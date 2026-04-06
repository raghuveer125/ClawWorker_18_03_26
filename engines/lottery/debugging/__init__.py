"""Debugging module — structured logging, stepwise debug traces, and failure buckets."""

from .logger import setup_logger, log_cycle, TRACE
from .trace import CycleTracer, FailureBucket

__all__ = ["setup_logger", "log_cycle", "TRACE", "CycleTracer", "FailureBucket"]

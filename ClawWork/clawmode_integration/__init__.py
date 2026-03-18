"""
ClawMode Integration — ClawWork economic tracking for nanobot.

Imports are resolved lazily so optional runtime dependencies do not break
unrelated test discovery or project health checks.
"""

from importlib import import_module


_EXPORT_MAP = {
    "ClawWorkAgentLoop": ("clawmode_integration.agent_loop", "ClawWorkAgentLoop"),
    "ClawWorkState": ("clawmode_integration.tools", "ClawWorkState"),
    "DecideActivityTool": ("clawmode_integration.tools", "DecideActivityTool"),
    "SubmitWorkTool": ("clawmode_integration.tools", "SubmitWorkTool"),
    "LearnTool": ("clawmode_integration.tools", "LearnTool"),
    "GetStatusTool": ("clawmode_integration.tools", "GetStatusTool"),
    "TaskClassifier": ("clawmode_integration.task_classifier", "TaskClassifier"),
    "TrackedProvider": ("clawmode_integration.provider_wrapper", "TrackedProvider"),
}

__all__ = list(_EXPORT_MAP)


def __getattr__(name):
    if name not in _EXPORT_MAP:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

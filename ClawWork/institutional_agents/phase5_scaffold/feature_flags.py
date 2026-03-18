from contracts import FeatureFlags


def resolve_flags(raw: dict) -> FeatureFlags:
    return FeatureFlags(
        institutional_agent_enabled=bool(raw.get("institutional_agent_enabled", False)),
        shadow_mode_enabled=bool(raw.get("shadow_mode_enabled", True)),
    )

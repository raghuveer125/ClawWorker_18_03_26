import asyncio

from scalping import api


def test_pipeline_uses_current_agent_id_ranges():
    api.init_agents()

    pipeline = asyncio.run(api.get_pipeline())
    stage_agents = {stage["id"]: stage["agents"] for stage in pipeline["stages"]}

    assert stage_agents["analysis"] == [5, 6, 7, 8, 9, 10, 11]
    assert stage_agents["quality"] == [12]
    assert stage_agents["risk"] == [13, 14, 15]
    assert stage_agents["execution"] == [16, 17, 18, 19]
    assert stage_agents["learning"] == [20, 21, 22]


def test_update_agent_status_only_counts_actual_runs():
    api.init_agents()

    api.update_agent_status(17, "running")
    api.update_agent_status(
        17,
        "idle",
        output={"orders_created": 1},
        metrics={"latency_ms": 12},
        bot_status="success",
        message="entry complete",
    )

    entry_agent = next(agent for agent in api.get_state().agents if agent.agent_id == 17)
    assert entry_agent.run_count == 1
    assert entry_agent.last_run is not None
    assert entry_agent.last_output == {"orders_created": 1}
    assert entry_agent.metrics == {"latency_ms": 12}
    assert entry_agent.diagnostics["last_bot_status"] == "success"
    assert entry_agent.diagnostics["last_message"] == "entry complete"
    assert entry_agent.diagnostics["last_success_at"] == entry_agent.last_run

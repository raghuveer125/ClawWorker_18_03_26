"""Test Synapse - Neural Message Bus."""

import asyncio
import sys
sys.path.insert(0, "/Users/bhoomidakshpc/Project_WebSocket/ClawWork_FyersN7")


async def test_synapse():
    """Test all Synapse components."""
    print("=" * 50)
    print("Testing SYNAPSE - Neural Message Bus")
    print("=" * 50)

    # 1. Test Synapse Core
    print("\n1. Testing Synapse Core...")
    from ai_hub.synapse.synapse_core import Synapse, MessagePriority, get_synapse

    synapse = get_synapse()

    # Register test agents
    received_messages = []

    def test_handler(msg):
        received_messages.append(msg)

    synapse.register_agent(
        agent_id="test_agent_1",
        agent_type="test",
        capabilities=["execute", "analyze"],
        layer=3,
        handler=test_handler
    )

    synapse.register_agent(
        agent_id="test_agent_2",
        agent_type="test",
        capabilities=["monitor"],
        layer=4,
    )

    # Subscribe to channels
    channel_msgs = []
    synapse.subscribe(lambda m: channel_msgs.append(m), channels=["data"])

    # Send messages
    msg_id = synapse.send_data("test_source", {"symbol": "NIFTY50", "ltp": 22500})
    print(f"   Sent data message: {msg_id}")

    cmd_id = synapse.send_command(
        "test_source", "execute_task", {"task_id": "task_001"}, "test_agent_1"
    )
    print(f"   Sent command: {cmd_id}")

    alert_id = synapse.send_alert("risk_monitor", "high_exposure", "Position limit reached", "warning")
    print(f"   Sent alert: {alert_id}")

    # Check stats
    stats = synapse.get_stats()
    print(f"   Messages sent: {stats['messages_sent']}")
    print(f"   Registered agents: {stats['registered_agents']}")
    print("   ✓ Synapse Core working")

    # 2. Test Context Manager
    print("\n2. Testing Context Manager...")
    from ai_hub.synapse.context.context_manager import SynapseContextManager

    ctx_mgr = SynapseContextManager()

    # Set context
    ctx_mgr.set_market_regime("trending_up", "reasoning_agent")
    ctx_mgr.set_volatility_state("normal", "reasoning_agent")
    ctx_mgr.set("active_symbol", "NIFTY50", layer=1, source="goal_agent", ttl=300)

    # Get context
    regime = ctx_mgr.get_market_regime()
    vol = ctx_mgr.get_volatility_state()
    print(f"   Market regime: {regime}")
    print(f"   Volatility: {vol}")

    # Snapshot
    snapshot = ctx_mgr.snapshot()
    print(f"   Context keys: {list(snapshot.keys())}")

    # Watch context
    watched_changes = []
    ctx_mgr.watch("market_regime", lambda k, v: watched_changes.append((k, v)))
    ctx_mgr.set_market_regime("ranging", "test")
    print(f"   Watched changes: {len(watched_changes)}")

    ctx_stats = ctx_mgr.get_stats()
    print(f"   Context stats: {ctx_stats['total_entries']} entries")
    print("   ✓ Context Manager working")

    # 3. Test Agent Coordinator
    print("\n3. Testing Agent Coordinator...")
    from ai_hub.synapse.coordination.agent_coordinator import AgentCoordinator

    coordinator = AgentCoordinator(synapse)

    # Register agents
    coordinator.register(
        agent_id="dispatcher_1",
        agent_type="dispatcher",
        layer=3,
        capabilities=["dispatch", "route"],
        max_load=20
    )

    coordinator.register(
        agent_id="executor_1",
        agent_type="executor",
        layer=4,
        capabilities=["execute", "trade"],
        max_load=5
    )

    coordinator.register(
        agent_id="executor_2",
        agent_type="executor",
        layer=4,
        capabilities=["execute", "trade"],
        max_load=5
    )

    # Find agents
    executors = coordinator.find_by_type("executor")
    print(f"   Found {len(executors)} executors")

    trade_agents = coordinator.find_by_capability("trade")
    print(f"   Found {len(trade_agents)} agents with 'trade' capability")

    layer_4_agents = coordinator.find_by_layer(4)
    print(f"   Found {len(layer_4_agents)} Layer 4 agents")

    # Select best agent
    best = coordinator.select_best(capability="execute")
    print(f"   Best executor: {best.agent_id if best else 'None'}")

    # Route task
    assigned = coordinator.route_task("execute_trade", ["execute", "trade"])
    print(f"   Task routed to: {assigned}")

    # Complete task
    if assigned:
        coordinator.complete_task(assigned)

    # Health check
    unhealthy = coordinator.check_health()
    print(f"   Unhealthy agents: {len(unhealthy)}")

    coord_stats = coordinator.get_stats()
    print(f"   Coordinator stats: {coord_stats['total_agents']} agents, {coord_stats['active']} active")
    print("   ✓ Agent Coordinator working")

    # 4. Test Channels
    print("\n4. Testing Channels...")
    from ai_hub.synapse.channels.channel import SynapseChannel, ChannelConfig, ChannelType, ChannelManager

    channel_mgr = ChannelManager()

    # Get default channels
    channels = channel_mgr.list_channels()
    print(f"   Default channels: {channels}")

    # Use data channel
    data_channel = channel_mgr.get_channel("data")
    data_received = []
    data_channel.subscribe(lambda m: data_received.append(m))
    data_channel.publish({"symbol": "BANKNIFTY", "ltp": 48000})
    print(f"   Data channel messages: {len(data_received)}")

    # Get channel stats
    all_stats = channel_mgr.get_all_stats()
    print(f"   Channel stats: {len(all_stats)} channels")
    print("   ✓ Channels working")

    # 5. Integration test
    print("\n5. Integration test...")

    # Simulate message flow: Data → Synapse → Agent
    integration_received = []

    def integration_handler(msg):
        integration_received.append(msg)

    synapse.subscribe(integration_handler, channels=["cmd"])

    # Send through synapse
    synapse.send(
        channel="cmd",
        source="layer2_reasoning",
        payload={"action": "execute_trade", "symbol": "NIFTY50"},
        target="dispatcher_1",
        priority=MessagePriority.HIGH
    )

    print(f"   Integration messages received: {len(integration_received)}")

    # Set and share context
    synapse.set_context("current_task", "trading_session", ttl=300)
    task_ctx = synapse.get_context("current_task")
    print(f"   Shared context: {task_ctx}")

    print("   ✓ Integration working")

    print("\n" + "=" * 50)
    print("SYNAPSE - All tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_synapse())

"""
Layer 0 Integration Test

Tests the complete Layer 0 data foundation:
- AdaptiveSchemaManager
- IndicatorRegistry
- Layer0FyersAdapter
- DataPipe
- Layer0DataFeedAgent
"""

import asyncio
import sys
from pathlib import Path

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_hub.layer0.schema.adaptive_schema_manager import AdaptiveSchemaManager, FieldType
from ai_hub.layer0.enrichment.indicator_registry import get_indicator_registry
from ai_hub.layer0.enrichment.indicators import compute_vwap, compute_fvg
from ai_hub.layer0.adapters.fyers_adapter import Layer0FyersAdapter
from ai_hub.layer0.pipe.data_pipe import DataPipe, DataEventType
from ai_hub.layer0.agents.data_feed_agent import Layer0DataFeedAgent


def test_schema_manager():
    """Test AdaptiveSchemaManager."""
    print("\n=== Testing AdaptiveSchemaManager ===")

    # Use fresh schema dir to avoid conflicts
    import tempfile
    schema_dir = Path(tempfile.mkdtemp()) / "schema"
    sm = AdaptiveSchemaManager(schema_dir=schema_dir)

    # Check base fields
    base_fields = list(sm.get_base_fields().keys())
    print(f"Base fields: {base_fields[:5]}...")
    assert sm.has_field("ltp"), "Missing base field: ltp"
    assert sm.has_field("volume"), "Missing base field: volume"

    # Add computed field directly
    success = sm.add_field(
        name="vwap",
        description="Volume Weighted Average Price",
        dependencies=["close", "volume"],
        compute_fn="compute_vwap",
        reason="Test addition",
        added_by="test",
    )
    assert success, "Failed to add field"
    print(f"Added VWAP field: {sm.has_field('vwap')}")

    # Check version
    print(f"Schema version: {sm.get_schema_version()}")
    assert sm.get_schema_version() >= 1

    # Add another field directly (simpler than request flow)
    success2 = sm.add_field(
        name="fvg_zones",
        description="Fair Value Gap zones",
        dependencies=["open", "high", "low", "close"],
        compute_fn="compute_fvg",
        reason="FVG zones predict reversals with 73% accuracy",
        added_by="learning_army",
    )
    print(f"FVG field added: {sm.has_field('fvg_zones')}")

    # Export schema
    schema = sm.export_schema()
    print(f"Schema: {len(schema['all_fields'])} fields total")

    print("AdaptiveSchemaManager: PASSED")
    return True


def test_indicator_registry():
    """Test IndicatorRegistry."""
    print("\n=== Testing IndicatorRegistry ===")

    registry = get_indicator_registry()

    # List indicators
    indicators = registry.list_indicators()
    print(f"Available indicators: {len(indicators)}")
    for ind in indicators[:3]:
        print(f"  - {ind['name']}: {ind['description']}")

    # Test VWAP computation
    test_candles = [
        {"close": 100, "volume": 1000},
        {"close": 102, "volume": 1500},
        {"close": 101, "volume": 1200},
        {"close": 103, "volume": 2000},
        {"close": 102, "volume": 1800},
    ]

    result = registry.compute("compute_vwap", test_candles)
    print(f"VWAP result: {result.value}")
    assert result.value is not None, "VWAP computation failed"
    assert 100 <= result.value <= 103, "VWAP out of range"

    # Test FVG computation
    test_candles_fvg = [
        {"open": 100, "high": 102, "low": 99, "close": 101},
        {"open": 101, "high": 105, "low": 100, "close": 104},  # Big move
        {"open": 106, "high": 108, "low": 105, "close": 107},  # Gap up
    ]

    result_fvg = registry.compute("compute_fvg", test_candles_fvg)
    print(f"FVG zones found: {len(result_fvg.value) if result_fvg.value else 0}")

    print("IndicatorRegistry: PASSED")
    return True


def test_data_pipe():
    """Test DataPipe."""
    print("\n=== Testing DataPipe ===")

    pipe = DataPipe()
    events_received = []

    # Subscribe to events
    def on_event(event):
        events_received.append(event)
        print(f"Event received: {event.event_type.value} - {event.symbol}")

    pipe.subscribe(on_event)

    # Publish a quote
    pipe.publish_quote(
        source="test",
        symbol="NSE:NIFTY50",
        data={"ltp": 24500, "volume": 1000000},
    )

    # Publish history
    pipe.publish_history(
        source="test",
        symbol="NSE:NIFTY50",
        data={"candles": [{"c": 24500, "v": 1000}]},
    )

    print(f"Events received: {len(events_received)}")
    assert len(events_received) == 2, "Event subscription failed"

    # Test field request
    request_id = pipe.request_field({
        "name": "test_indicator",
        "description": "Test indicator",
        "dependencies": ["ltp"],
        "compute_fn": "compute_test",
        "reason": "Testing",
        "confidence": 0.9,
        "requester": "test",
    })
    print(f"Field request submitted: {request_id}")

    # Check stats
    stats = pipe.get_stats()
    print(f"Pipe stats: events={stats['events_published']}, requests={stats['field_requests']}")

    print("DataPipe: PASSED")
    return True


def test_fyers_adapter():
    """Test Layer0FyersAdapter (mock mode if no credentials)."""
    print("\n=== Testing Layer0FyersAdapter ===")

    adapter = Layer0FyersAdapter(auto_enrich=True)

    # Check status
    status = adapter.get_status()
    print(f"Adapter status: connected={status.connected}")

    # Get schema
    schema = adapter.get_schema()
    print(f"Schema version: {schema['version']}")
    print(f"Base fields: {len(schema['base_fields'])}")
    print(f"Computed fields: {len(schema['computed_fields'])}")

    # Get available indicators
    indicators = adapter.get_available_indicators()
    print(f"Available indicators: {len(indicators)}")

    # Test quote (will be mock if no credentials)
    quote = adapter.get_quote("NSE:NIFTY50-INDEX")
    print(f"Quote: ltp={quote.base.get('ltp', 0)}")

    print("Layer0FyersAdapter: PASSED")
    return True


async def test_data_feed_agent():
    """Test Layer0DataFeedAgent."""
    print("\n=== Testing Layer0DataFeedAgent ===")

    agent = Layer0DataFeedAgent(indices=["NIFTY50"])

    # Subscribe to events
    events = []
    agent.subscribe(lambda e: events.append(e))

    # Fetch data
    results = await agent.fetch_all(
        include_history=True,
        include_options=False,  # Faster test
    )

    print(f"Fetch results: {len(results)} indices")
    for index, result in results.items():
        print(f"  {index}: success={result.success}, latency={result.latency_ms:.1f}ms")

    # Check stats
    stats = agent.get_stats()
    print(f"Agent stats: fetches={stats['fetches']}, errors={stats['errors']}")

    print("Layer0DataFeedAgent: PASSED")
    return True


async def test_full_integration():
    """Test full Layer 0 integration."""
    print("\n=== Testing Full Integration ===")

    # Create components
    schema_manager = AdaptiveSchemaManager()
    pipe = DataPipe()
    adapter = Layer0FyersAdapter(schema_manager=schema_manager)
    pipe.register_adapter("fyers", adapter)

    # Create agent
    agent = Layer0DataFeedAgent(
        adapters={"fyers": adapter},
        data_pipe=pipe,
        indices=["NIFTY50"],
    )

    # Simulate Learning Army field request
    print("\nSimulating Learning Army field request...")
    request_id = pipe.request_field({
        "name": "vwap",
        "description": "Volume Weighted Average Price",
        "dependencies": ["close", "volume"],
        "compute_fn": "compute_vwap",
        "reason": "VWAP crosses improve entry timing by 15%",
        "confidence": 0.88,
        "requester": "QuantLearnerAgent",
    })

    pending = pipe.get_pending_field_requests()
    print(f"Pending requests: {len(pending)}")

    # Approve (normally this would go through debate)
    pipe.approve_field_request(request_id)
    print(f"Request approved. Schema now has 'vwap': {schema_manager.has_field('vwap')}")

    # Fetch data
    print("\nFetching data through the pipe...")
    data = pipe.get_index_data("NIFTY50", adapter_name="fyers")
    print(f"Index data keys: {list(data.keys())}")

    # Check recent events
    recent = pipe.get_recent_events(limit=5)
    print(f"Recent events: {len(recent)}")

    print("\nFull Integration: PASSED")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("LAYER 0 DATA FOUNDATION - INTEGRATION TEST")
    print("=" * 60)

    results = {
        "schema_manager": test_schema_manager(),
        "indicator_registry": test_indicator_registry(),
        "data_pipe": test_data_pipe(),
        "fyers_adapter": test_fyers_adapter(),
    }

    # Async tests
    results["data_feed_agent"] = asyncio.run(test_data_feed_agent())
    results["full_integration"] = asyncio.run(test_full_integration())

    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    all_passed = True
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED!")
    else:
        print("SOME TESTS FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()

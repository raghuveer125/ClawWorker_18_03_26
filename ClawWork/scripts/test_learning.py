#!/usr/bin/env python3
"""
Test script for Learning & Knowledge feature
Creates a test learning entry to verify the full pipeline works
"""

import json
import os
from datetime import datetime

# Configuration
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DATA_DIR = os.path.join(PROJECT_DIR, "livebench", "data", "agent_data")
TEST_AGENT = "gpt-4o-inline-test"

def test_create_learning_entry():
    """Create a test learning entry"""
    agent_dir = os.path.join(AGENT_DATA_DIR, TEST_AGENT)
    memory_dir = os.path.join(agent_dir, "memory")
    memory_file = os.path.join(memory_dir, "memory.jsonl")

    # Create memory directory if it doesn't exist
    os.makedirs(memory_dir, exist_ok=True)
    print(f"✅ Created memory directory: {memory_dir}")

    # Create test entry
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().isoformat(),
        "topic": "Test Learning Entry",
        "knowledge": "This is a test learning entry to verify the Learning & Knowledge feature works correctly. The system should display this entry in the Learning Timeline on the frontend dashboard. If you can see this entry, the full pipeline from backend storage to frontend display is working properly. This entry was created by the test_learning.py script."
    }

    # Write to memory.jsonl
    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"✅ Created learning entry in: {memory_file}")
    print(f"   Topic: {entry['topic']}")
    print(f"   Date: {entry['date']}")
    print(f"   Knowledge length: {len(entry['knowledge'])} chars")

    return True


def test_read_learning_entries():
    """Read learning entries to verify they were saved"""
    agent_dir = os.path.join(AGENT_DATA_DIR, TEST_AGENT)
    memory_file = os.path.join(agent_dir, "memory", "memory.jsonl")

    if not os.path.exists(memory_file):
        print("❌ memory.jsonl not found")
        return False

    entries = []
    with open(memory_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))

    print(f"\n📚 Found {len(entries)} learning entries:")
    for i, entry in enumerate(entries, 1):
        print(f"   {i}. [{entry['date']}] {entry['topic']}")

    return len(entries) > 0


def test_api_endpoint():
    """Test the API endpoint"""
    import requests

    url = f"http://localhost:8000/api/agents/{TEST_AGENT}/learning"
    try:
        resp = requests.get(url, timeout=5)
        data = resp.json()

        print(f"\n🔗 API Response from {url}:")
        print(f"   Status: {resp.status_code}")
        print(f"   Entries: {len(data.get('entries', []))}")

        if data.get('entries'):
            for entry in data['entries']:
                print(f"   - [{entry.get('date')}] {entry.get('topic')}")

        return resp.status_code == 200 and len(data.get('entries', [])) > 0
    except Exception as e:
        print(f"❌ API error: {e}")
        return False


def main():
    print("=" * 50)
    print("🧪 Learning & Knowledge Feature Test")
    print("=" * 50)
    print(f"Agent: {TEST_AGENT}")
    print(f"Data dir: {AGENT_DATA_DIR}")
    print()

    # Test 1: Create entry
    print("📝 Test 1: Creating learning entry...")
    test_create_learning_entry()

    # Test 2: Read entries
    print("\n📖 Test 2: Reading learning entries...")
    test_read_learning_entries()

    # Test 3: API endpoint
    print("\n🌐 Test 3: Testing API endpoint...")
    api_works = test_api_endpoint()

    print("\n" + "=" * 50)
    if api_works:
        print("✅ SUCCESS! Full pipeline working")
        print("   Refresh http://localhost:3000/learning to see the entry")
    else:
        print("⚠️  API returned empty - check if server is running")
    print("=" * 50)


if __name__ == "__main__":
    main()

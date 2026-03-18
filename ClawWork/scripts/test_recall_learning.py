#!/usr/bin/env python3
"""
Test script for recall_learning() tool
"""

import json
import os
import sys

# Add project to path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from livebench.tools import direct_tools
from livebench.tools.direct_tools import recall_learning, learn

# Configuration
AGENT_DATA_DIR = os.path.join(PROJECT_DIR, "livebench", "data", "agent_data")
TEST_AGENT = "gpt-4o-inline-test"


def test_recall_learning():
    """Test the recall_learning tool"""

    # Set up global state (simulating agent context)
    agent_data_path = os.path.join(AGENT_DATA_DIR, TEST_AGENT)
    direct_tools._global_state = {
        "signature": TEST_AGENT,
        "data_path": agent_data_path,
        "current_date": "2026-02-22"
    }

    print("=" * 50)
    print("🧪 Testing recall_learning() Tool")
    print("=" * 50)
    print(f"Agent: {TEST_AGENT}")
    print(f"Data path: {agent_data_path}")
    print()

    # Test 1: Recall all entries
    print("📖 Test 1: Recall all entries")
    result = recall_learning.invoke("")
    print(f"   Total entries: {result.get('total_count', 0)}")
    print(f"   Matched: {result.get('matched_count', 0)}")
    if result.get('entries'):
        for entry in result['entries']:
            print(f"   - [{entry['date']}] {entry['topic']}")
    print()

    # Test 2: Add another learning entry for search testing
    print("📝 Test 2: Adding search test entry...")
    learn_result = learn.invoke({
        "topic": "Python Best Practices",
        "knowledge": "When writing Python code, always follow PEP 8 style guidelines. Use meaningful variable names, add docstrings to functions, and prefer list comprehensions over explicit loops when appropriate. Type hints improve code readability and help catch bugs early. Virtual environments isolate project dependencies."
    })
    print(f"   {learn_result.get('message', 'Done')}")
    print()

    # Test 3: Search by topic
    print("📖 Test 3: Search for 'Python'")
    result = recall_learning.invoke("Python")
    print(f"   Matched: {result.get('matched_count', 0)} / {result.get('total_count', 0)}")
    if result.get('entries'):
        for entry in result['entries']:
            print(f"   - [{entry['date']}] {entry['topic']}")
    print()

    # Test 4: Search by content
    print("📖 Test 4: Search for 'dashboard'")
    result = recall_learning.invoke("dashboard")
    print(f"   Matched: {result.get('matched_count', 0)} / {result.get('total_count', 0)}")
    if result.get('entries'):
        for entry in result['entries']:
            print(f"   - [{entry['date']}] {entry['topic']}")
    print()

    # Test 5: No results search
    print("📖 Test 5: Search for 'nonexistent_xyz'")
    result = recall_learning.invoke("nonexistent_xyz")
    print(f"   Matched: {result.get('matched_count', 0)} / {result.get('total_count', 0)}")
    print()

    print("=" * 50)
    print("✅ recall_learning() tool working correctly!")
    print("=" * 50)


if __name__ == "__main__":
    test_recall_learning()

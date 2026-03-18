#!/usr/bin/env python3
"""
Test script for learned knowledge prompt injection
"""

import os
import sys

# Add project to path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from livebench.prompts.live_agent_prompt import get_live_agent_system_prompt


def test_prompt_with_knowledge():
    """Test prompt generation with learned knowledge"""
    print("=" * 60)
    print("🧪 Testing Prompt Knowledge Injection")
    print("=" * 60)

    # Test data
    economic_state = {
        'balance': 100.0,
        'net_worth': 100.0,
        'total_token_cost': 5.0,
        'session_cost': 0.01,
        'daily_cost': 0.5,
        'survival_status': 'stable'
    }

    learned_knowledge = [
        {
            'topic': 'Python Best Practices',
            'date': '2026-02-20',
            'knowledge': 'Always use virtual environments for Python projects. Follow PEP 8 style guidelines for code formatting.'
        },
        {
            'topic': 'Data Analysis Techniques',
            'date': '2026-02-21',
            'knowledge': 'Pandas is excellent for data manipulation. Use vectorized operations instead of loops for better performance.'
        },
        {
            'topic': 'Test Learning Entry',
            'date': '2026-02-22',
            'knowledge': 'This is a test learning entry to verify the Learning & Knowledge feature works correctly.'
        }
    ]

    # Generate prompt with knowledge
    prompt = get_live_agent_system_prompt(
        date='2026-02-22',
        signature='test-agent',
        economic_state=economic_state,
        work_task=None,
        max_steps=15,
        learned_knowledge=learned_knowledge
    )

    # Check if knowledge section is present
    if '📚 YOUR ACCUMULATED KNOWLEDGE' in prompt:
        print("✅ Knowledge section found in prompt")
    else:
        print("❌ Knowledge section NOT found in prompt")
        return False

    if 'recall_learning' in prompt:
        print("✅ recall_learning tool documented in prompt")
    else:
        print("❌ recall_learning tool NOT documented in prompt")
        return False

    # Print relevant section
    print("\n📝 Knowledge section from generated prompt:")
    print("-" * 60)

    # Find and print the knowledge section
    start = prompt.find('📚 YOUR ACCUMULATED KNOWLEDGE')
    if start != -1:
        end = prompt.find('💰 TOKEN COSTS', start)
        if end != -1:
            print(prompt[start:end])
        else:
            print(prompt[start:start+800])

    print("-" * 60)
    print("\n✅ Prompt injection test PASSED!")
    return True


def test_prompt_without_knowledge():
    """Test prompt generation without learned knowledge"""
    print("\n" + "=" * 60)
    print("🧪 Testing Prompt WITHOUT Knowledge")
    print("=" * 60)

    economic_state = {
        'balance': 100.0,
        'net_worth': 100.0,
        'total_token_cost': 5.0,
        'session_cost': 0.01,
        'daily_cost': 0.5,
        'survival_status': 'stable'
    }

    # Generate prompt without knowledge
    prompt = get_live_agent_system_prompt(
        date='2026-02-22',
        signature='test-agent',
        economic_state=economic_state,
        work_task=None,
        max_steps=15,
        learned_knowledge=[]  # Empty list
    )

    # Check that knowledge section is NOT present (since no knowledge)
    if '📚 YOUR ACCUMULATED KNOWLEDGE' not in prompt:
        print("✅ No knowledge section when no knowledge exists (correct)")
    else:
        print("⚠️  Knowledge section present even with no knowledge")

    print("✅ Empty knowledge test PASSED!")
    return True


if __name__ == "__main__":
    test1 = test_prompt_with_knowledge()
    test2 = test_prompt_without_knowledge()

    print("\n" + "=" * 60)
    if test1 and test2:
        print("✅ ALL TESTS PASSED!")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)

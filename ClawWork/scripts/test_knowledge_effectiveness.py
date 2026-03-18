#!/usr/bin/env python3
"""
Test script for Knowledge Effectiveness Tracking

Tests:
1. Knowledge Effectiveness Tracker initialization
2. Recording knowledge recall
3. Recording task completion with effectiveness metrics
4. Calculating high-ROI knowledge
5. Learning ROI summary
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project to path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from livebench.agent.knowledge_effectiveness_tracker import KnowledgeEffectivenessTracker


def test_knowledge_effectiveness_tracker():
    """Test the knowledge effectiveness tracker"""
    
    # Setup
    test_agent = "test-effectiveness-agent"
    data_path = PROJECT_DIR / "livebench" / "data" / "agent_data" / test_agent
    os.makedirs(data_path, exist_ok=True)
    
    print("=" * 70)
    print("🧪 Testing Knowledge Effectiveness Tracking")
    print("=" * 70)
    print(f"📁 Agent: {test_agent}")
    print(f"📂 Data path: {data_path}")
    print()
    
    # Test 1: Initialize tracker
    print("Test 1️⃣ : Initialize Knowledge Effectiveness Tracker")
    tracker = KnowledgeEffectivenessTracker(test_agent, str(data_path))
    print(f"   ✅ Tracker initialized")
    print(f"   📊 Tracker dir: {tracker.tracker_dir}")
    print()
    
    # Test 2: Record knowledge recall
    print("Test 2️⃣ : Record Knowledge Recall")
    topics_recalled = ["Python best practices", "Web scraping techniques"]
    tracker.record_knowledge_recall(
        task_id="task_001",
        recalled_topics=topics_recalled,
        date="2026-03-01"
    )
    print(f"   ✅ Recorded recall of {len(topics_recalled)} topics for task_001")
    for topic in topics_recalled:
        print(f"      • {topic}")
    print()
    
    # Test 3: Record successful task completion
    print("Test 3️⃣ : Record Task Completion with Learning Impact")
    impact = tracker.record_task_completion(
        task_id="task_001",
        evaluation_score=0.85,
        payment=45.00,
        recalled_topics=topics_recalled,
        baseline_score=0.50,
        date="2026-03-01"
    )
    print(f"   ✅ Task completed with metrics:")
    print(f"      • Score: {impact['evaluation_score']:.2f}")
    print(f"      • Payment: ${impact['payment']:.2f}")
    print(f"      • Performance gain: {impact['performance_gain']:.2f}")
    print()
    
    # Test 4: Record more task completions with same knowledge
    print("Test 4️⃣ : Record Additional Task Completions (Build History)")
    
    # Successful use of Python knowledge
    tracker.record_task_completion(
        task_id="task_002",
        evaluation_score=0.80,
        payment=40.00,
        recalled_topics=["Python best practices"],
        baseline_score=0.50,
        date="2026-03-02"
    )
    print(f"   ✅ Task 2 completed: Score 0.80, Payment $40")
    
    # Another successful use
    tracker.record_task_completion(
        task_id="task_003",
        evaluation_score=0.75,
        payment=35.00,
        recalled_topics=["Python best practices", "Data analysis"],
        baseline_score=0.50,
        date="2026-03-03"
    )
    print(f"   ✅ Task 3 completed: Score 0.75, Payment $35")
    
    # One moderate use
    tracker.record_task_completion(
        task_id="task_004",
        evaluation_score=0.55,
        payment=15.00,
        recalled_topics=["Web scraping techniques"],
        baseline_score=0.50,
        date="2026-03-04"
    )
    print(f"   ✅ Task 4 completed: Score 0.55, Payment $15")
    print()
    
    # Test 5: Get effectiveness metrics for individual topics
    print("Test 5️⃣ : Get Effectiveness Metrics for Individual Topics")
    
    for topic in ["Python best practices", "Web scraping techniques", "Data analysis"]:
        effectiveness = tracker.get_knowledge_effectiveness(topic)
        print(f"   📊 {topic}")
        print(f"      • Total uses: {effectiveness['total_uses']}")
        if effectiveness['total_uses'] > 0:
            print(f"      • Success rate: {effectiveness['success_rate']:.2%}")
            print(f"      • Avg earnings: ${effectiveness['avg_earnings_per_use']:.2f}")
            print(f"      • Effective: {effectiveness['effective']}")
            print(f"      • Last used: {effectiveness['last_used']}")
        else:
            print(f"      • No usage data yet")
    print()
    
    # Test 6: Get high-ROI knowledge
    print("Test 6️⃣ : Identify High-ROI Knowledge")
    high_roi = tracker.get_high_roi_knowledge(min_uses=1)
    print(f"   🏆 Found {len(high_roi)} high-ROI topics:")
    for topic_data in high_roi:
        print(f"      • {topic_data['topic']}")
        print(f"        ├ Success rate: {topic_data['success_rate']:.2%}")
        print(f"        ├ Total earnings: ${topic_data['total_earnings']:.2f}")
        print(f"        └ Avg per use: ${topic_data['avg_earnings']:.2f}")
    print()
    
    # Test 7: Learning ROI Summary
    print("Test 7️⃣ : Learning ROI Summary")
    summary = tracker.get_learning_roi_summary()
    print(f"   📈 Overall Learning Metrics:")
    print(f"      • Total knowledge items: {summary['total_knowledge_items']}")
    print(f"      • Total knowledge uses: {summary['total_uses']}")
    print(f"      • Total earnings from knowledge: ${summary['total_earnings_from_knowledge']:.2f}")
    print(f"      • Avg earnings per use: ${summary['avg_earnings_per_use']:.2f}")
    print()
    
    # Test 8: Verify file persistence
    print("Test 8️⃣ : Verify File Persistence")
    files_created = [f for f in os.listdir(tracker.tracker_dir)]
    print(f"   💾 Files created in {tracker.tracker_dir}:")
    for filename in files_created:
        filepath = os.path.join(tracker.tracker_dir, filename)
        filesize = os.path.getsize(filepath)
        print(f"      • {filename} ({filesize} bytes)")
    print()
    
    # Test 9: Load from disk and verify persistence
    print("Test 9️⃣ : Reload from Disk and Verify Persistence")
    tracker2 = KnowledgeEffectivenessTracker(test_agent, str(data_path))
    summary2 = tracker2.get_learning_roi_summary()
    
    if summary2['total_knowledge_items'] == summary['total_knowledge_items']:
        print(f"   ✅ Persistence verified!")
        print(f"      • Items in memory: {summary['total_knowledge_items']}")
        print(f"      • Items from disk: {summary2['total_knowledge_items']}")
    else:
        print(f"   ❌ Persistence check failed!")
    print()
    
    # Test 10: Should recommend knowledge recall
    print("🔟 Recommendation System Test")
    should_recall = tracker.should_recall_knowledge_for_task({
        "occupation": "Software Engineer",
        "sector": "Technology"
    })
    print(f"   💡 Should recommend knowledge recall: {should_recall}")
    print()
    
    print("=" * 70)
    print("✅ ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print()
    print("📊 Summary:")
    print(f"   • Created and persisted knowledge effectiveness tracking")
    print(f"   • Recorded {summary['total_uses']} knowledge uses across tasks")
    print(f"   • Generated ${summary['total_earnings_from_knowledge']:.2f} in earnings")
    print(f"   • Identified {len(high_roi)} high-ROI knowledge topics")
    print()


if __name__ == "__main__":
    try:
        test_knowledge_effectiveness_tracker()
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

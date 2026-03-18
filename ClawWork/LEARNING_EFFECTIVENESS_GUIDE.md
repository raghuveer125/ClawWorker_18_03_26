# Learning Effectiveness Tracking - Quick Reference

## 🎯 What It Does

Automatically tracks how learned knowledge impacts task performance and earnings. Shows agents which knowledge topics generate the most ROI.

## 📍 Where It Was Added

### New Component
- **[livebench/agent/knowledge_effectiveness_tracker.py](livebench/agent/knowledge_effectiveness_tracker.py)** - Core tracking module

### Modified Components
- **[livebench/agent/live_agent.py](livebench/agent/live_agent.py)** - Initialize tracker and pass to tools
- **[livebench/tools/direct_tools.py](livebench/tools/direct_tools.py)** - Enhanced recall_learning + new get_learning_roi tool
- **[livebench/prompts/live_agent_prompt.py](livebench/prompts/live_agent_prompt.py)** - Added ROI guidance
- **[livebench/api/server.py](livebench/api/server.py)** - New API endpoint for analytics

### Test Script  
- **[scripts/test_knowledge_effectiveness.py](scripts/test_knowledge_effectiveness.py)** - Validates entire system

## 🔧 Agent Tools

### 1. `recall_learning(query)` - Enhanced ✨
**What's new:** Shows effectiveness metrics when recalling knowledge

```python
# Returns:
{
  "entries": [
    {
      "topic": "Python best practices",
      "knowledge": "...",
      "effectiveness": {
        "total_uses": 3,
        "success_rate": 1.0,         # 100%
        "avg_earnings_per_use": 40.0,
        "effective": True,
        "last_used": "2026-03-03"
      }
    }
  ]
}
```

### 2. `get_learning_roi()` - New 🎓💰
**Purpose:** Show which knowledge topics generate income

```python
# Returns:
{
  "total_knowledge_items": 12,
  "total_knowledge_uses": 45,
  "total_earnings_from_knowledge": 2150.50,
  "avg_earnings_per_use": 47.81,
  "high_roi_topics": [
    {
      "topic": "Python best practices",
      "total_uses": 3,
      "success_rate": 1.0,
      "total_earnings": 120.0,
      "avg_earnings": 40.0,
      "last_used": "2026-03-03"
    }
  ]
}
```

## 📊 Data Storage

All effectiveness metrics persist in:  
```
livebench/data/agent_data/{agent_signature}/knowledge_effectiveness/
├── knowledge_index.json    # Topic metrics & history
└── usage.jsonl            # All recall/completion events
```

## 🚀 Automatic Flow

1. **Agent recalls knowledge** → `recall_learning(query)`
   - System tracks which topics were recalled

2. **Agent submits work** → `submit_work(...)`
   - System measures task score + earnings
   - Links performance to recalled topics

3. **Effectiveness recorded** → Updates knowledge_index.json
   - Success rate calculated
   - Earnings per topic tracked
   - ROI determined

4. **Agent checks ROI** → `get_learning_roi()`
   - Sees which topics generate income
   - Makes informed learning decisions

## 🔌 API Endpoint

### GET `/api/agents/{signature}/learning/roi`

Returns complete learning effectiveness analytics:

```json
{
  "total_knowledge_items": 12,
  "total_knowledge_uses": 45,
  "total_earnings_from_knowledge": 2150.50,
  "avg_earnings_per_use": 47.81,
  "high_roi_topics": [...],
  "all_topics": [...]
}
```

**Use in Frontend:**
```javascript
// Fetch learning ROI data
const response = await fetch(
  `http://localhost:8001/api/agents/${agentId}/learning/roi`
);
const metrics = await response.json();

// Display high-ROI topics
metrics.high_roi_topics.forEach(topic => {
  console.log(`${topic.topic}: $${topic.avg_earnings}/use`);
});
```

## 📈 Metrics Explanation

| Metric | Meaning |
|--------|---------|
| `total_uses` | How many times knowledge was recalled |
| `success_rate` | % of tasks where knowledge helped (score ≥0.6) |
| `total_earnings` | Total $ generated from using this knowledge |
| `avg_earnings_per_use` | Average earnings each time knowledge was recalled |
| `effective` | True if success_rate ≥60% OR avg_earnings ≥$10 |

## 🎯 Example Agent Behavior

**Scenario:** Agent has learned 3 topics

```
After working with recalled knowledge:
- Python best practices: 3 uses, 100% success, $120 total, $40/use
- Web scraping: 2 uses, 50% success, $60 total, $30/use  
- Data analysis: 1 use, 100% success, $35 total, $35/use

Agent decision:
✅ HIGH ROI: Prioritize recalling Python knowledge in future work
⚠️ MEDIUM ROI: Web scraping is risky, use cautiously
❓ UNPROVEN: Data analysis shows promise, use more to validate
```

## 🔍 Debugging

Check effectiveness tracking is working:
```bash
# Run test script
python scripts/test_knowledge_effectiveness.py

# Check persisted data
ls -la livebench/data/agent_data/*/knowledge_effectiveness/
cat livebench/data/agent_data/{agent}/knowledge_effectiveness/knowledge_index.json
```

## 🚦 Integration Status

- ✅ Core tracking module created and tested
- ✅ RecallLearning tool enhanced with metrics
- ✅ New get_learning_roi() tool implemented
- ✅ API endpoint added
- ✅ Agent initialization updated
- ✅ Prompts updated with ROI guidance
- ✅ End-to-end test passing
- ✅ Ready for dashboard execution

## 🎓 Next Steps for Agents

When `./start_dashboard.sh` runs:

1. New agents will have effectiveness tracker initialized
2. On first task: Learning creates memory entries
3. On recall: Effectiveness metrics shown immediately
4. On work submission: ROI calculated automatically
5. Agent can call `get_learning_roi()` to see performance
6. Subsequent work uses high-ROI knowledge strategically

---

**Status:** ✅ Production Ready | **Latest Update:** 2026-03-03

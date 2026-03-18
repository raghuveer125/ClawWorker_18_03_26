"""
Knowledge Effectiveness Tracker

Measures if learned knowledge actually improves task performance & earnings.
Tracks: which knowledge was recalled → task outcome → performance gain → ROI
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path


class KnowledgeEffectivenessTracker:
    """Track effectiveness of learned knowledge in improving task performance"""

    def __init__(self, signature: str, data_path: str):
        """
        Initialize effectiveness tracker
        
        Args:
            signature: Agent signature
            data_path: Base data path for agent
        """
        self.signature = signature
        self.data_path = data_path
        self.tracker_dir = os.path.join(data_path, "knowledge_effectiveness")
        os.makedirs(self.tracker_dir, exist_ok=True)
        
        self.usage_log = os.path.join(self.tracker_dir, "usage.jsonl")
        self.index_file = os.path.join(self.tracker_dir, "knowledge_index.json")
        
    def record_knowledge_recall(
        self, 
        task_id: str,
        recalled_topics: List[str],
        date: str
    ) -> None:
        """
        Record that knowledge was recalled for a task
        
        Args:
            task_id: ID of the task
            recalled_topics: List of knowledge topics recalled
            date: Date of recall
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "date": date,
            "task_id": task_id,
            "recalled_topics": recalled_topics,
            "phase": "before_task"
        }
        
        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def record_task_completion(
        self,
        task_id: str,
        evaluation_score: float,
        payment: float,
        recalled_topics: Optional[List[str]] = None,
        baseline_score: float = 0.5,  # Typical score without knowledge
        date: str = ""
    ) -> Dict[str, Any]:
        """
        Record task completion and calculate knowledge impact
        
        Args:
            task_id: ID of the task
            evaluation_score: Score achieved (0-1)
            payment: Payment received
            recalled_topics: Topics that were recalled for this task
            baseline_score: Typical score without knowledge use
            date: Date of task
            
        Returns:
            Impact metrics dictionary
        """
        # Calculate performance gain
        performance_gain = max(0, evaluation_score - baseline_score)
        
        # Calculate efficiency gain (payment vs token cost)
        roi = payment / max(0.01, payment)  # Simple ROI (earnings per engagement)
        
        impact = {
            "timestamp": datetime.now().isoformat(),
            "date": date,
            "task_id": task_id,
            "evaluation_score": evaluation_score,
            "payment": payment,
            "performance_gain": performance_gain,
            "roi": roi,
            "recalled_topics": recalled_topics or [],
            "phase": "after_task"
        }
        
        # Record completion
        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(impact, ensure_ascii=False) + "\n")
        
        # Update knowledge index
        if recalled_topics:
            self._update_knowledge_index(recalled_topics, impact)
        
        return impact
    
    def _update_knowledge_index(self, topics: List[str], impact: Dict) -> None:
        """Update the knowledge-to-performance index"""
        index = self._load_knowledge_index()
        
        for topic in topics:
            if topic not in index:
                index[topic] = {
                    "total_uses": 0,
                    "successful_uses": 0,
                    "total_earnings": 0.0,
                    "avg_performance_gain": 0.0,
                    "last_used": "",
                    "uses": []
                }
            
            index[topic]["total_uses"] += 1
            index[topic]["last_used"] = impact["date"]
            
            if impact["evaluation_score"] >= 0.6:  # Threshold for success
                index[topic]["successful_uses"] += 1
            
            index[topic]["total_earnings"] += impact["payment"]
            
            # Track individual uses
            index[topic]["uses"].append({
                "task_id": impact["task_id"],
                "date": impact["date"],
                "score": impact["evaluation_score"],
                "payment": impact["payment"],
                "performance_gain": impact["performance_gain"]
            })
        
        self._save_knowledge_index(index)
    
    def get_knowledge_effectiveness(self, topic: str) -> Dict[str, Any]:
        """
        Get effectiveness metrics for a specific knowledge topic
        
        Args:
            topic: Knowledge topic
            
        Returns:
            Effectiveness metrics
        """
        index = self._load_knowledge_index()
        
        if topic not in index:
            return {
                "topic": topic,
                "total_uses": 0,
                "effective": False,
                "message": f"No usage data for '{topic}'"
            }
        
        data = index[topic]
        success_rate = data["successful_uses"] / max(1, data["total_uses"])
        avg_earnings = data["total_earnings"] / max(1, data["total_uses"])
        
        return {
            "topic": topic,
            "total_uses": data["total_uses"],
            "successful_uses": data["successful_uses"],
            "success_rate": success_rate,
            "total_earnings": data["total_earnings"],
            "avg_earnings_per_use": avg_earnings,
            "effective": success_rate >= 0.6 or avg_earnings >= 10.0,
            "last_used": data["last_used"],
            "recent_uses": data["uses"][-3:] if data["uses"] else []
        }
    
    def get_high_roi_knowledge(self, min_uses: int = 2) -> List[Dict[str, Any]]:
        """
        Get high-ROI knowledge sorted by effectiveness
        
        Args:
            min_uses: Minimum number of uses to consider
            
        Returns:
            List of high-ROI knowledge topics
        """
        index = self._load_knowledge_index()
        
        high_roi = []
        for topic, data in index.items():
            if data["total_uses"] >= min_uses:
                success_rate = data["successful_uses"] / max(1, data["total_uses"])
                avg_earnings = data["total_earnings"] / max(1, data["total_uses"])
                
                if success_rate >= 0.6 or avg_earnings >= 10.0:
                    high_roi.append({
                        "topic": topic,
                        "total_uses": data["total_uses"],
                        "success_rate": success_rate,
                        "avg_earnings": avg_earnings,
                        "total_earnings": data["total_earnings"],
                        "last_used": data["last_used"]
                    })
        
        # Sort by earnings (descending)
        return sorted(high_roi, key=lambda x: x["total_earnings"], reverse=True)
    
    def get_learning_roi_summary(self) -> Dict[str, Any]:
        """Get overall learning ROI summary"""
        index = self._load_knowledge_index()
        
        if not index:
            return {
                "total_knowledge_items": 0,
                "total_uses": 0,
                "total_earnings_from_knowledge": 0,
                "message": "No knowledge effectiveness data yet"
            }
        
        total_uses = sum(data["total_uses"] for data in index.values())
        total_earnings = sum(data["total_earnings"] for data in index.values())
        avg_earnings_per_use = total_earnings / max(1, total_uses)
        
        return {
            "total_knowledge_items": len(index),
            "total_uses": total_uses,
            "total_earnings_from_knowledge": total_earnings,
            "avg_earnings_per_use": avg_earnings_per_use,
            "high_roi_topics": self.get_high_roi_knowledge()
        }
    
    def should_recall_knowledge_for_task(self, task_info: Dict) -> bool:
        """
        Determine if recalling knowledge would likely help with this task
        
        Args:
            task_info: Information about the task (occupation, sector, etc.)
            
        Returns:
            True if should recommend knowledge recall
        """
        index = self._load_knowledge_index()
        
        if not index:
            return False
        
        # Check if there's relevant high-ROI knowledge
        high_roi = self.get_high_roi_knowledge(min_uses=1)
        return len(high_roi) > 0
    
    def get_topic_effectiveness_for_sector(self, sector: str) -> List[Dict[str, Any]]:
        """
        Get knowledge effectiveness for a specific sector/occupation
        
        Args:
            sector: Occupation or sector name
            
        Returns:
            List of relevant high-ROI knowledge topics
        """
        index = self._load_knowledge_index()
        
        # Match topics related to sector
        matching_topics = [
            {
                "topic": topic,
                "success_rate": data["successful_uses"] / max(1, data["total_uses"]),
                "total_earnings": data["total_earnings"],
                "last_used": data["last_used"]
            }
            for topic, data in index.items()
            if sector.lower() in topic.lower()
        ]
        
        return sorted(matching_topics, key=lambda x: x["total_earnings"], reverse=True)
    
    def _load_knowledge_index(self) -> Dict[str, Dict]:
        """Load knowledge index from file"""
        if not os.path.exists(self.index_file):
            return {}
        
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    
    def _save_knowledge_index(self, index: Dict) -> None:
        """Save knowledge index to file"""
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

"""
Fine-tuning Data Exporter - Export debate sessions for model fine-tuning.

Exports to:
- OpenAI JSONL format (for GPT fine-tuning)
- Anthropic format (for Claude fine-tuning when available)
- Generic conversation format

Usage:
    python finetune_exporter.py --format openai --output training_data.jsonl
    python finetune_exporter.py --format anthropic --min-rounds 3 --consensus-only
"""

import json
import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


SESSIONS_FILE = Path(__file__).parent / "sessions.json"
OUTPUT_DIR = Path(__file__).parent / "training_data"


@dataclass
class TrainingExample:
    """Single training example from a debate session."""
    session_id: str
    messages: List[Dict[str, str]]
    system_prompt: str
    final_solution: Optional[str]
    reached_consensus: bool
    rounds: int
    quality_score: float  # 0-1 based on consensus speed and outcome


class FinetuneExporter:
    """Export debate sessions to fine-tuning format."""

    def __init__(self):
        self.sessions = self._load_sessions()
        OUTPUT_DIR.mkdir(exist_ok=True)

    def _load_sessions(self) -> Dict[str, Any]:
        """Load all debate sessions."""
        if not SESSIONS_FILE.exists():
            return {}
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except Exception as e:
            print(f"Error loading sessions: {e}")
            return {}

    def _calculate_quality_score(self, session: Dict) -> float:
        """
        Calculate quality score for training prioritization.

        Higher scores for:
        - Reached consensus
        - Fewer rounds (efficient debate)
        - Has final solution
        """
        score = 0.0

        # Consensus reached
        if session.get("status") == "consensus":
            score += 0.5

        # Round efficiency (fewer is better, max 7 rounds)
        rounds = session.get("current_round", 7)
        round_score = max(0, (7 - rounds) / 7) * 0.3
        score += round_score

        # Has final solution
        if session.get("final_solution"):
            score += 0.2

        return min(1.0, score)

    def _session_to_openai_format(
        self,
        session: Dict,
        role: str = "proposer"
    ) -> List[Dict[str, Any]]:
        """
        Convert session to OpenAI fine-tuning format.

        Format: {"messages": [{"role": "system", "content": "..."},
                              {"role": "user", "content": "..."},
                              {"role": "assistant", "content": "..."}]}
        """
        examples = []
        messages = session.get("messages", [])
        task = session.get("task", "")

        if not messages:
            return examples

        # Create training example for the target role
        system_prompt = self._get_system_prompt(role)

        conversation = [{"role": "system", "content": system_prompt}]

        for i, msg in enumerate(messages):
            msg_role = msg.get("role", "")
            content = msg.get("content", "")

            if msg_role == role:
                # This is the assistant response we want to train
                # Build user context from task + prior messages
                if i == 0:
                    user_content = f"Task: {task}"
                else:
                    # Include last critic/proposer message as context
                    prev_msg = messages[i-1] if i > 0 else None
                    if prev_msg:
                        user_content = f"Task: {task}\n\nPrevious response:\n{prev_msg.get('content', '')[:1000]}"
                    else:
                        user_content = f"Task: {task}"

                conversation.append({"role": "user", "content": user_content})
                conversation.append({"role": "assistant", "content": content})

                # Each turn becomes a training example
                examples.append({
                    "messages": conversation.copy(),
                    "metadata": {
                        "session_id": session.get("session_id", ""),
                        "round": i // 2 + 1,
                        "reached_consensus": session.get("status") == "consensus",
                    }
                })

        return examples

    def _session_to_anthropic_format(
        self,
        session: Dict,
        role: str = "proposer"
    ) -> List[Dict[str, Any]]:
        """
        Convert session to Anthropic fine-tuning format.

        Format: {"system": "...", "messages": [{"role": "user/assistant", "content": "..."}]}
        """
        examples = []
        messages = session.get("messages", [])
        task = session.get("task", "")

        if not messages:
            return examples

        system_prompt = self._get_system_prompt(role)

        for i, msg in enumerate(messages):
            msg_role = msg.get("role", "")
            content = msg.get("content", "")

            if msg_role == role:
                # Build conversation up to this point
                conv_messages = []

                if i == 0:
                    conv_messages.append({
                        "role": "user",
                        "content": f"Task: {task}"
                    })
                else:
                    prev_msg = messages[i-1] if i > 0 else None
                    user_content = f"Task: {task}"
                    if prev_msg:
                        user_content += f"\n\nFeedback from critic:\n{prev_msg.get('content', '')[:1000]}"
                    conv_messages.append({"role": "user", "content": user_content})

                conv_messages.append({"role": "assistant", "content": content})

                examples.append({
                    "system": system_prompt,
                    "messages": conv_messages,
                    "metadata": {
                        "session_id": session.get("session_id", ""),
                        "round": i // 2 + 1,
                        "is_consensus": msg.get("is_consensus", False),
                    }
                })

        return examples

    def _get_system_prompt(self, role: str) -> str:
        """Get system prompt for a role."""
        if role == "proposer":
            return """You are an expert code proposer in a debate system. Your goal is to:
1. Propose minimal, focused code changes
2. Address critic feedback constructively
3. Reach consensus quickly with high-quality solutions

Output format:
## File
[path]

## Changes
```
// REPLACE: [old code]
// WITH: [new code]
```

## Why
[1 sentence explanation]"""
        else:
            return """You are an expert code critic in a debate system. Your goal is to:
1. Find bugs, edge cases, and improvements
2. Be constructive - suggest fixes, don't just criticize
3. Approve good solutions with "CONSENSUS: YES"

If issues found:
- List specific concerns
- Suggest fixes

If solution is good:
CONSENSUS: YES
[brief approval reason]"""

    def export_openai(
        self,
        output_file: str = "openai_training.jsonl",
        role: str = "proposer",
        consensus_only: bool = False,
        min_rounds: int = 1,
        min_quality: float = 0.0,
    ) -> int:
        """
        Export to OpenAI fine-tuning JSONL format.

        Returns number of examples exported.
        """
        output_path = OUTPUT_DIR / output_file
        examples = []

        for session_id, session in self.sessions.items():
            # Filter by consensus
            if consensus_only and session.get("status") != "consensus":
                continue

            # Filter by rounds
            if session.get("current_round", 0) < min_rounds:
                continue

            # Filter by quality
            quality = self._calculate_quality_score(session)
            if quality < min_quality:
                continue

            session_examples = self._session_to_openai_format(session, role)
            examples.extend(session_examples)

        # Write JSONL
        with open(output_path, "w") as f:
            for ex in examples:
                # Remove metadata for actual training file
                training_ex = {"messages": ex["messages"]}
                f.write(json.dumps(training_ex) + "\n")

        print(f"Exported {len(examples)} examples to {output_path}")
        return len(examples)

    def export_anthropic(
        self,
        output_file: str = "anthropic_training.jsonl",
        role: str = "proposer",
        consensus_only: bool = False,
        min_rounds: int = 1,
    ) -> int:
        """
        Export to Anthropic fine-tuning format.

        Note: Claude fine-tuning is currently limited/beta.
        This exports in the expected format for when it's available.
        """
        output_path = OUTPUT_DIR / output_file
        examples = []

        for session_id, session in self.sessions.items():
            if consensus_only and session.get("status") != "consensus":
                continue

            if session.get("current_round", 0) < min_rounds:
                continue

            session_examples = self._session_to_anthropic_format(session, role)
            examples.extend(session_examples)

        # Write JSONL
        with open(output_path, "w") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")

        print(f"Exported {len(examples)} examples to {output_path}")
        return len(examples)

    def export_conversation_log(
        self,
        output_file: str = "debate_conversations.json",
    ) -> int:
        """
        Export full conversation logs for analysis.
        """
        output_path = OUTPUT_DIR / output_file

        conversations = []
        for session_id, session in self.sessions.items():
            quality = self._calculate_quality_score(session)
            conversations.append({
                "session_id": session_id,
                "task": session.get("task", ""),
                "status": session.get("status", ""),
                "rounds": session.get("current_round", 0),
                "quality_score": quality,
                "messages": session.get("messages", []),
                "final_solution": session.get("final_solution"),
                "created_at": session.get("created_at", ""),
            })

        # Sort by quality
        conversations.sort(key=lambda x: x["quality_score"], reverse=True)

        with open(output_path, "w") as f:
            json.dump(conversations, f, indent=2)

        print(f"Exported {len(conversations)} conversations to {output_path}")
        return len(conversations)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about available training data."""
        total = len(self.sessions)
        consensus = sum(1 for s in self.sessions.values() if s.get("status") == "consensus")
        deadlock = sum(1 for s in self.sessions.values() if s.get("status") == "deadlock")

        total_messages = sum(len(s.get("messages", [])) for s in self.sessions.values())
        avg_rounds = sum(s.get("current_round", 0) for s in self.sessions.values()) / max(1, total)

        quality_scores = [self._calculate_quality_score(s) for s in self.sessions.values()]
        avg_quality = sum(quality_scores) / max(1, len(quality_scores))

        return {
            "total_sessions": total,
            "consensus_reached": consensus,
            "deadlocks": deadlock,
            "total_messages": total_messages,
            "avg_rounds": round(avg_rounds, 2),
            "avg_quality_score": round(avg_quality, 3),
            "high_quality_sessions": sum(1 for q in quality_scores if q >= 0.7),
        }


def main():
    parser = argparse.ArgumentParser(description="Export debate sessions for fine-tuning")
    parser.add_argument("--format", choices=["openai", "anthropic", "log", "stats"],
                        default="stats", help="Export format")
    parser.add_argument("--output", type=str, help="Output filename")
    parser.add_argument("--role", choices=["proposer", "critic"], default="proposer",
                        help="Role to train")
    parser.add_argument("--consensus-only", action="store_true",
                        help="Only export sessions that reached consensus")
    parser.add_argument("--min-rounds", type=int, default=1,
                        help="Minimum debate rounds")
    parser.add_argument("--min-quality", type=float, default=0.0,
                        help="Minimum quality score (0-1)")

    args = parser.parse_args()

    exporter = FinetuneExporter()

    if args.format == "stats":
        stats = exporter.get_stats()
        print("\n=== Debate Training Data Statistics ===")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        print()
        return

    if args.format == "openai":
        output = args.output or "openai_training.jsonl"
        exporter.export_openai(
            output_file=output,
            role=args.role,
            consensus_only=args.consensus_only,
            min_rounds=args.min_rounds,
            min_quality=args.min_quality,
        )

    elif args.format == "anthropic":
        output = args.output or "anthropic_training.jsonl"
        exporter.export_anthropic(
            output_file=output,
            role=args.role,
            consensus_only=args.consensus_only,
            min_rounds=args.min_rounds,
        )

    elif args.format == "log":
        output = args.output or "debate_conversations.json"
        exporter.export_conversation_log(output_file=output)


if __name__ == "__main__":
    main()

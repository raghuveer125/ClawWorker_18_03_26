"""
Debate Engine - Orchestrates multi-LLM consensus discussions.
LLMs debate until they reach agreement on the best solution.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import anthropic
import openai

from model_router import ModelRouter, ModelTier
from data_provider import get_data_provider
from code_executor import get_code_executor


class DebateStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    CONSENSUS = "consensus"
    DEADLOCK = "deadlock"
    ERROR = "error"


class MessageRole(Enum):
    PROPOSER = "proposer"
    CRITIC = "critic"
    SYSTEM = "system"


@dataclass
class DebateMessage:
    role: MessageRole
    provider: str
    model: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    tokens_used: int = 0
    is_consensus: bool = False
    concerns: List[str] = field(default_factory=list)


@dataclass
class DebateSession:
    session_id: str
    task: str
    project_path: str
    status: DebateStatus = DebateStatus.PENDING
    messages: List[DebateMessage] = field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 7
    final_solution: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # Store provider info for resume
    proposer_provider: str = "anthropic"
    critic_provider: str = "openai"
    last_error_at: Optional[str] = None  # "proposer" or "critic"


class DebateEngine:
    """
    Orchestrates debate between multiple LLMs.
    Supports Claude and OpenAI models with auto-switching.
    """

    SESSIONS_FILE = Path(__file__).parent / "sessions.json"

    def __init__(
        self,
        anthropic_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        self.anthropic_client = None
        self.openai_client = None
        self.model_router = ModelRouter()
        self.sessions: Dict[str, DebateSession] = {}
        self.data_provider = get_data_provider()
        self.code_executor = get_code_executor()
        self._load_sessions()

        if anthropic_key:
            self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        if openai_key:
            self.openai_client = openai.OpenAI(api_key=openai_key)

    def configure_keys(self, anthropic_key: str = None, openai_key: str = None):
        """Configure API keys at runtime."""
        if anthropic_key:
            self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        if openai_key:
            self.openai_client = openai.OpenAI(api_key=openai_key)

    def _load_sessions(self):
        """Load sessions from disk."""
        if not self.SESSIONS_FILE.exists():
            return
        try:
            data = json.loads(self.SESSIONS_FILE.read_text())
            for sid, sdata in data.items():
                messages = [
                    DebateMessage(
                        role=MessageRole(m["role"]),
                        provider=m["provider"],
                        model=m["model"],
                        content=m["content"],
                        timestamp=m.get("timestamp", ""),
                        tokens_used=m.get("tokens_used", 0),
                        is_consensus=m.get("is_consensus", False),
                        concerns=m.get("concerns", []),
                    )
                    for m in sdata.get("messages", [])
                ]
                self.sessions[sid] = DebateSession(
                    session_id=sid,
                    task=sdata["task"],
                    project_path=sdata["project_path"],
                    status=DebateStatus(sdata["status"]),
                    messages=messages,
                    current_round=sdata.get("current_round", 0),
                    max_rounds=sdata.get("max_rounds", 7),
                    final_solution=sdata.get("final_solution"),
                    created_at=sdata.get("created_at", ""),
                    proposer_provider=sdata.get("proposer_provider", "anthropic"),
                    critic_provider=sdata.get("critic_provider", "openai"),
                    last_error_at=sdata.get("last_error_at"),
                )
        except Exception as e:
            print(f"Failed to load sessions: {e}")

    def _save_session(self, session: DebateSession):
        """Save session to disk."""
        try:
            data = {}
            if self.SESSIONS_FILE.exists():
                data = json.loads(self.SESSIONS_FILE.read_text())

            data[session.session_id] = {
                "task": session.task,
                "project_path": session.project_path,
                "status": session.status.value,
                "messages": [
                    {
                        "role": m.role.value,
                        "provider": m.provider,
                        "model": m.model,
                        "content": m.content,
                        "timestamp": m.timestamp,
                        "tokens_used": m.tokens_used,
                        "is_consensus": m.is_consensus,
                        "concerns": m.concerns,
                    }
                    for m in session.messages
                ],
                "current_round": session.current_round,
                "max_rounds": session.max_rounds,
                "final_solution": session.final_solution,
                "created_at": session.created_at,
                "proposer_provider": session.proposer_provider,
                "critic_provider": session.critic_provider,
                "last_error_at": session.last_error_at,
            }
            self.SESSIONS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Failed to save session: {e}")

    def _get_project_context(self, project_path: str, task: str = "", max_files: int = 5) -> str:
        """Read only relevant project files based on task keywords."""
        path = Path(project_path)
        if not path.exists():
            return f"Project path not found: {project_path}"

        # Extract keywords from task for relevance scoring
        task_lower = task.lower()
        keywords = [w for w in task_lower.split() if len(w) > 3]

        context_parts = []
        scored_files = []

        # Skip patterns
        skip_patterns = ["node_modules", "venv", ".venv", "__pycache__", "dist", ".git", "build", ".next"]
        extensions = {".py", ".js", ".jsx", ".ts", ".tsx"}  # Removed .json, .md to reduce noise

        for file in path.rglob("*"):
            if not file.is_file() or file.suffix not in extensions:
                continue
            if any(skip in str(file) for skip in skip_patterns):
                continue

            # Score file relevance based on name and path matching task keywords
            file_str = str(file).lower()
            score = sum(1 for kw in keywords if kw in file_str)

            # Boost important files
            if "main" in file_str or "app" in file_str or "index" in file_str:
                score += 2
            if "config" in file_str or "settings" in file_str:
                score += 1

            scored_files.append((score, file))

        # Sort by relevance score (highest first)
        scored_files.sort(key=lambda x: x[0], reverse=True)

        for _, file in scored_files[:max_files]:
            try:
                content = file.read_text(errors="ignore")
                # Only take first 800 chars per file to reduce tokens
                if len(content) > 800:
                    content = content[:800] + "\n... (truncated)"
                context_parts.append(f"# {file.relative_to(path)}\n{content}")
            except Exception:
                continue

        if not context_parts:
            return "No relevant source files found."

        return "\n\n".join(context_parts)

    async def _call_anthropic(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int]:
        """Call Anthropic API."""
        if not self.anthropic_client:
            raise ValueError("Anthropic API key not configured")

        response = self.anthropic_client.messages.create(
            model=model,
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return content, tokens

    async def _call_openai(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int]:
        """Call OpenAI API."""
        if not self.openai_client:
            raise ValueError("OpenAI API key not configured")

        response = self.openai_client.chat.completions.create(
            model=model,
            max_tokens=2500,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0
        return content, tokens

    async def _call_llm(
        self,
        provider: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int]:
        """Route LLM call to appropriate provider."""
        if provider.lower() == "anthropic":
            return await self._call_anthropic(model, system_prompt, user_prompt)
        elif provider.lower() == "openai":
            return await self._call_openai(model, system_prompt, user_prompt)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _get_market_data_context(self) -> str:
        """Get market data context for validation."""
        try:
            return self.data_provider.get_sample_data_for_validation()
        except Exception as e:
            return f"[Market data unavailable: {e}]"

    def _validate_proposal(self, proposal: str) -> str:
        """Validate proposal code against real data."""
        try:
            result = self.code_executor.validate_proposal(proposal)
            return self.code_executor.format_validation_for_llm(result)
        except Exception as e:
            return f"[Validation unavailable: {e}]"

    def _build_proposer_prompt(
        self,
        task: str,
        project_context: str,
        debate_history: List[DebateMessage],
        round_num: int,
        market_data: str = "",
    ) -> tuple[str, str]:
        """Build concise prompts for the proposer LLM."""

        system = """You are the PROPOSER in a code debate. Be MINIMAL - show only changed code.

OUTPUT FORMAT (use exactly):
## File
[Full path, e.g., frontend/src/pages/SwingAnalysis.jsx]

## Changes
Show ONLY the function(s) or section(s) you're modifying. Use this format:
```
// REPLACE this function:
const oldFunction = () => { ... }

// WITH:
const newFunction = () => {
  // your new implementation
}
```

## Why
[1 sentence on why this fixes the issue]

RULES:
- Show ONLY changed code, NOT the entire file
- Use "REPLACE X WITH Y" format for clarity
- Keep changes minimal and focused
- If validation data is provided, reference it to prove your solution works
- Goal: reach consensus quickly"""

        # Only include last critic message for context (reduce tokens)
        last_critic = ""
        last_validation = ""
        for msg in reversed(debate_history):
            if msg.role == MessageRole.CRITIC:
                last_critic = f"\n\nCRITIC's last feedback:\n{msg.content[:800]}"
                break

        # Get market data for validation context
        if not market_data:
            market_data = self._get_market_data_context()

        user = f"""TASK: {task}

MARKET DATA (use to validate your solution):
{market_data[:1500]}

RELEVANT CODE:
{project_context[:1500]}
{last_critic}

Round {round_num}. {"Propose your solution." if round_num == 1 else "Address concerns and refine."}"""

        return system, user

    def _build_critic_prompt(
        self,
        task: str,
        project_context: str,
        current_proposal: str,
        debate_history: List[DebateMessage],
        round_num: int,
        validation_result: str = "",
    ) -> tuple[str, str]:
        """Build concise prompts for the critic LLM."""

        # After round 4, be more lenient to avoid deadlock
        leniency_note = ""
        if round_num >= 4:
            leniency_note = f"\n\nIMPORTANT: This is round {round_num}. To avoid deadlock, APPROVE if the solution is 80% correct. Minor issues can be fixed later."

        system = f"""You are the CRITIC in a code debate. Be PRAGMATIC, not perfect.

OUTPUT FORMAT (use exactly):

If critical bugs exist:
## STATUS: CONCERNS
[One specific issue that MUST be fixed]
## Fix: [Exact fix in 1 line]

If solution works (even if not perfect):
## STATUS: APPROVED
[1 sentence confirming it works]

APPROVAL CRITERIA:
- VALIDATION passed? → APPROVE (unless security/crash bug)
- Logic correct for main case? → APPROVE
- Edge cases handled? → Nice to have, not required
- Code style/naming? → IGNORE completely
{leniency_note}

ONLY reject for: crashes, security holes, infinite loops, wrong logic."""

        # Only include the proposal and last exchange for context
        prev_round = ""
        if round_num > 1:
            for msg in reversed(debate_history):
                if msg.role == MessageRole.CRITIC:
                    prev_round = f"\nYour last feedback:\n{msg.content[:400]}"
                    break

        # Validate the proposal against real data
        if not validation_result:
            validation_result = self._validate_proposal(current_proposal)

        # Highlight validation success
        validation_note = ""
        if "swing_highs_count" in validation_result or "success" in validation_result.lower():
            validation_note = "\n\n>>> VALIDATION PASSED - Code executed successfully. This strongly suggests APPROVAL unless you find a critical bug. <<<"

        user = f"""TASK: {task}

PROPOSAL (Round {round_num}):
{current_proposal[:2500]}

VALIDATION AGAINST REAL MARKET DATA:
{validation_result}{validation_note}
{prev_round}

Respond with STATUS: APPROVED or STATUS: CONCERNS."""

        return system, user

    def _parse_consensus(self, critic_response: str) -> tuple[bool, List[str]]:
        """Parse critic response to check for consensus."""
        response_upper = critic_response.upper()

        is_approved = "STATUS: APPROVED" in response_upper or "APPROVED" in response_upper[:200]

        concerns = []
        if not is_approved and "CONCERNS" in response_upper:
            # Extract concerns
            lines = critic_response.split("\n")
            in_issues = False
            for line in lines:
                if "issues found" in line.lower() or "concerns" in line.lower():
                    in_issues = True
                    continue
                if in_issues and line.strip().startswith(("1.", "2.", "3.", "-", "*")):
                    concerns.append(line.strip())
                if "suggested" in line.lower() or "improvement" in line.lower():
                    in_issues = False

        return is_approved, concerns

    async def run_debate(
        self,
        session_id: str,
        task: str,
        project_path: str,
        proposer_provider: str = "anthropic",
        critic_provider: str = "openai",
        max_rounds: int = 7,
        on_message: Optional[Callable[[DebateMessage], None]] = None,
    ) -> AsyncGenerator[DebateMessage, None]:
        """
        Run a debate session between two LLMs.
        Yields messages as they are generated for real-time streaming.
        """

        # Initialize session
        session = DebateSession(
            session_id=session_id,
            task=task,
            project_path=project_path,
            max_rounds=max_rounds,
            status=DebateStatus.IN_PROGRESS,
            proposer_provider=proposer_provider,
            critic_provider=critic_provider,
        )
        self.sessions[session_id] = session

        # Get project context
        project_context = self._get_project_context(project_path)

        # System message
        system_msg = DebateMessage(
            role=MessageRole.SYSTEM,
            provider="system",
            model="system",
            content=f"Starting debate on: {task}\nProject: {project_path}\nProposer: {proposer_provider} | Critic: {critic_provider}",
        )
        session.messages.append(system_msg)
        self._save_session(session)
        yield system_msg

        current_proposal = ""

        for round_num in range(1, max_rounds + 1):
            session.current_round = round_num

            # --- PROPOSER TURN ---
            proposer_model_info = self.model_router.get_model(
                provider=proposer_provider,
                task=task,
                code_context=project_context,
                debate_round=round_num,
            )

            system_prompt, user_prompt = self._build_proposer_prompt(
                task, project_context, session.messages, round_num
            )

            try:
                proposal, tokens = await self._call_llm(
                    proposer_provider,
                    proposer_model_info["model"],
                    system_prompt,
                    user_prompt,
                )
            except Exception as e:
                error_msg = DebateMessage(
                    role=MessageRole.SYSTEM,
                    provider="system",
                    model="system",
                    content=f"Error from proposer: {str(e)}",
                )
                session.messages.append(error_msg)
                session.status = DebateStatus.ERROR
                session.last_error_at = "proposer"
                self._save_session(session)
                yield error_msg
                return

            current_proposal = proposal
            proposer_msg = DebateMessage(
                role=MessageRole.PROPOSER,
                provider=proposer_provider,
                model=proposer_model_info["model"],
                content=proposal,
                tokens_used=tokens,
            )
            session.messages.append(proposer_msg)
            self._save_session(session)
            yield proposer_msg

            # --- CRITIC TURN ---
            critic_model_info = self.model_router.get_model(
                provider=critic_provider,
                task=task,
                code_context=project_context,
                debate_round=round_num,
            )

            system_prompt, user_prompt = self._build_critic_prompt(
                task, project_context, current_proposal, session.messages, round_num
            )

            try:
                critique, tokens = await self._call_llm(
                    critic_provider,
                    critic_model_info["model"],
                    system_prompt,
                    user_prompt,
                )
            except Exception as e:
                error_msg = DebateMessage(
                    role=MessageRole.SYSTEM,
                    provider="system",
                    model="system",
                    content=f"Error from critic: {str(e)}",
                )
                session.messages.append(error_msg)
                session.status = DebateStatus.ERROR
                session.last_error_at = "critic"
                self._save_session(session)
                yield error_msg
                return

            is_approved, concerns = self._parse_consensus(critique)

            critic_msg = DebateMessage(
                role=MessageRole.CRITIC,
                provider=critic_provider,
                model=critic_model_info["model"],
                content=critique,
                tokens_used=tokens,
                is_consensus=is_approved,
                concerns=concerns,
            )
            session.messages.append(critic_msg)
            self._save_session(session)
            yield critic_msg

            # Check for consensus
            if is_approved:
                session.status = DebateStatus.CONSENSUS
                session.final_solution = current_proposal

                consensus_msg = DebateMessage(
                    role=MessageRole.SYSTEM,
                    provider="system",
                    model="system",
                    content=f"CONSENSUS REACHED in round {round_num}! The solution has been approved.",
                    is_consensus=True,
                )
                session.messages.append(consensus_msg)
                self._save_session(session)
                yield consensus_msg
                return

            # Small delay between rounds
            await asyncio.sleep(0.5)

        # Max rounds reached without consensus
        session.status = DebateStatus.DEADLOCK
        deadlock_msg = DebateMessage(
            role=MessageRole.SYSTEM,
            provider="system",
            model="system",
            content=f"DEADLOCK: Max rounds ({max_rounds}) reached without consensus. Last proposal may still be valid - review manually.",
        )
        session.messages.append(deadlock_msg)
        session.final_solution = current_proposal
        self._save_session(session)
        yield deadlock_msg

    def get_session(self, session_id: str) -> Optional[DebateSession]:
        """Get a debate session by ID."""
        return self.sessions.get(session_id)

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get a summary of a debate session."""
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}

        total_tokens = sum(msg.tokens_used for msg in session.messages)

        return {
            "session_id": session.session_id,
            "status": session.status.value,
            "rounds": session.current_round,
            "total_messages": len(session.messages),
            "total_tokens": total_tokens,
            "has_consensus": session.status == DebateStatus.CONSENSUS,
            "final_solution": session.final_solution,
            "created_at": session.created_at,
            "can_resume": session.status in (DebateStatus.ERROR, DebateStatus.DEADLOCK),
            "last_error_at": session.last_error_at,
            "proposer_provider": session.proposer_provider,
            "critic_provider": session.critic_provider,
        }

    async def resume_debate(
        self,
        session_id: str,
        switch_provider: Optional[str] = None,
    ) -> AsyncGenerator[DebateMessage, None]:
        """
        Resume a debate session that encountered an error.
        Optionally switch the failing provider to a different one.

        Args:
            session_id: The session to resume
            switch_provider: If set, switch the failing LLM to this provider
        """
        session = self.sessions.get(session_id)
        if not session:
            error_msg = DebateMessage(
                role=MessageRole.SYSTEM,
                provider="system",
                model="system",
                content="Session not found. Cannot resume.",
            )
            yield error_msg
            return

        if session.status not in (DebateStatus.ERROR, DebateStatus.DEADLOCK):
            error_msg = DebateMessage(
                role=MessageRole.SYSTEM,
                provider="system",
                model="system",
                content=f"Session cannot be resumed (status: {session.status.value}).",
            )
            yield error_msg
            return

        is_deadlock = session.status == DebateStatus.DEADLOCK

        # Switch provider if requested
        if switch_provider:
            if session.last_error_at == "proposer":
                session.proposer_provider = switch_provider
            elif session.last_error_at == "critic":
                session.critic_provider = switch_provider
            elif is_deadlock:
                # For deadlock, switch the critic (who rejected)
                session.critic_provider = switch_provider

        # Remove the error/deadlock message from history
        if session.messages and session.messages[-1].role == MessageRole.SYSTEM:
            if "Error from" in session.messages[-1].content or "DEADLOCK" in session.messages[-1].content:
                session.messages.pop()

        # Get the last proposal (if critic failed)
        current_proposal = ""
        for msg in reversed(session.messages):
            if msg.role == MessageRole.PROPOSER:
                current_proposal = msg.content
                break

        # Extend max_rounds if resuming from deadlock
        if is_deadlock:
            session.max_rounds += 3  # Give 3 more rounds to reach consensus

        # Resume message
        resume_msg = DebateMessage(
            role=MessageRole.SYSTEM,
            provider="system",
            model="system",
            content=f"Resuming debate from round {session.current_round}. "
                    f"{'Extended to ' + str(session.max_rounds) + ' rounds. ' if is_deadlock else ''}"
                    f"Proposer: {session.proposer_provider} | Critic: {session.critic_provider}",
        )
        session.messages.append(resume_msg)
        session.status = DebateStatus.IN_PROGRESS
        self._save_session(session)
        yield resume_msg

        # Get project context
        project_context = self._get_project_context(session.project_path)

        # Determine where to resume from
        start_round = session.current_round
        retry_proposer = session.last_error_at == "proposer"

        for round_num in range(start_round, session.max_rounds + 1):
            session.current_round = round_num

            # --- PROPOSER TURN (only if we need to retry or it's a new round) ---
            if retry_proposer or round_num > start_round:
                proposer_model_info = self.model_router.get_model(
                    provider=session.proposer_provider,
                    task=session.task,
                    code_context=project_context,
                    debate_round=round_num,
                )

                system_prompt, user_prompt = self._build_proposer_prompt(
                    session.task, project_context, session.messages, round_num
                )

                try:
                    proposal, tokens = await self._call_llm(
                        session.proposer_provider,
                        proposer_model_info["model"],
                        system_prompt,
                        user_prompt,
                    )
                except Exception as e:
                    error_msg = DebateMessage(
                        role=MessageRole.SYSTEM,
                        provider="system",
                        model="system",
                        content=f"Error from proposer: {str(e)}",
                    )
                    session.messages.append(error_msg)
                    session.status = DebateStatus.ERROR
                    session.last_error_at = "proposer"
                    self._save_session(session)
                    yield error_msg
                    return

                current_proposal = proposal
                proposer_msg = DebateMessage(
                    role=MessageRole.PROPOSER,
                    provider=session.proposer_provider,
                    model=proposer_model_info["model"],
                    content=proposal,
                    tokens_used=tokens,
                )
                session.messages.append(proposer_msg)
                self._save_session(session)
                yield proposer_msg

            retry_proposer = False  # Only retry once

            # --- CRITIC TURN ---
            critic_model_info = self.model_router.get_model(
                provider=session.critic_provider,
                task=session.task,
                code_context=project_context,
                debate_round=round_num,
            )

            system_prompt, user_prompt = self._build_critic_prompt(
                session.task, project_context, current_proposal, session.messages, round_num
            )

            try:
                critique, tokens = await self._call_llm(
                    session.critic_provider,
                    critic_model_info["model"],
                    system_prompt,
                    user_prompt,
                )
            except Exception as e:
                error_msg = DebateMessage(
                    role=MessageRole.SYSTEM,
                    provider="system",
                    model="system",
                    content=f"Error from critic: {str(e)}",
                )
                session.messages.append(error_msg)
                session.status = DebateStatus.ERROR
                session.last_error_at = "critic"
                self._save_session(session)
                yield error_msg
                return

            is_approved, concerns = self._parse_consensus(critique)

            critic_msg = DebateMessage(
                role=MessageRole.CRITIC,
                provider=session.critic_provider,
                model=critic_model_info["model"],
                content=critique,
                tokens_used=tokens,
                is_consensus=is_approved,
                concerns=concerns,
            )
            session.messages.append(critic_msg)
            self._save_session(session)
            yield critic_msg

            # Check for consensus
            if is_approved:
                session.status = DebateStatus.CONSENSUS
                session.final_solution = current_proposal

                consensus_msg = DebateMessage(
                    role=MessageRole.SYSTEM,
                    provider="system",
                    model="system",
                    content=f"CONSENSUS REACHED in round {round_num}! The solution has been approved.",
                    is_consensus=True,
                )
                session.messages.append(consensus_msg)
                self._save_session(session)
                yield consensus_msg
                return

            await asyncio.sleep(0.5)

        # Max rounds reached
        session.status = DebateStatus.DEADLOCK
        deadlock_msg = DebateMessage(
            role=MessageRole.SYSTEM,
            provider="system",
            model="system",
            content=f"DEADLOCK: Max rounds ({session.max_rounds}) reached without consensus.",
        )
        session.messages.append(deadlock_msg)
        session.final_solution = current_proposal
        self._save_session(session)
        yield deadlock_msg

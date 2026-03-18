"""
LLM Debate Server - FastAPI backend with WebSocket support.
Provides real-time streaming of multi-LLM debates.
"""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import difflib
import re

from debate_engine import DebateEngine, DebateStatus, MessageRole
from code_executor import get_code_executor
from code_validator import get_validator


# Load environment variables from multiple locations
env_paths = [
    Path(__file__).parent.parent.parent / "ClawWork" / ".env",  # ClawWork_FyersN7/ClawWork/.env
    Path(__file__).parent / ".env",  # llm_debate/backend/.env
    Path.home() / ".env",  # Home directory
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        print(f"Loaded env from: {env_path}")


# Global state
debate_engine: Optional[DebateEngine] = None
active_connections: Dict[str, List[WebSocket]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the debate engine on startup with env keys."""
    global debate_engine

    # Auto-load keys from environment
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    debate_engine = DebateEngine(
        anthropic_key=anthropic_key,
        openai_key=openai_key,
    )

    if anthropic_key:
        print("Auto-configured Anthropic API from environment")
    if openai_key:
        print("Auto-configured OpenAI API from environment")

    yield
    # Cleanup on shutdown
    active_connections.clear()


app = FastAPI(
    title="LLM Debate System",
    description="Multi-LLM consensus through structured debate",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic Models ---

class ConfigureKeysRequest(BaseModel):
    anthropic_key: Optional[str] = None
    openai_key: Optional[str] = None
    project_path: Optional[str] = None  # Default project path


class StartDebateRequest(BaseModel):
    task: str
    project_path: str
    proposer_provider: str = "anthropic"
    critic_provider: str = "openai"
    max_rounds: int = 7


class DebateMessageResponse(BaseModel):
    role: str
    provider: str
    model: str
    content: str
    timestamp: str
    tokens_used: int
    is_consensus: bool
    concerns: List[str]


class ApplyCodeRequest(BaseModel):
    session_id: str
    preview_only: bool = True  # If True, just return diff; if False, apply changes
    validate_first: bool = True  # Run dry-run validation before applying
    run_tests: bool = False  # Run tests during validation (slower but safer)
    auto_apply: bool = False  # If True, auto-apply after validation passes


# --- REST Endpoints ---

@app.get("/api/")
async def api_root():
    return {
        "service": "LLM Debate System",
        "version": "1.0.0",
        "endpoints": {
            "configure": "POST /api/configure",
            "start_debate": "POST /api/debate/start",
            "get_session": "GET /api/debate/{session_id}",
            "websocket": "WS /ws/debate/{session_id}",
        },
    }


@app.post("/api/configure")
async def configure_keys(request: ConfigureKeysRequest):
    """Configure API keys for LLM providers and persist to .env file."""
    global debate_engine

    if not debate_engine:
        debate_engine = DebateEngine()

    configured = []
    env_file = Path(__file__).parent / ".env"

    # Load existing env content
    env_content = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env_content[key.strip()] = val.strip()

    # Update with new keys
    if request.anthropic_key:
        debate_engine.configure_keys(anthropic_key=request.anthropic_key)
        env_content["ANTHROPIC_API_KEY"] = request.anthropic_key
        configured.append("anthropic")

    if request.openai_key:
        debate_engine.configure_keys(openai_key=request.openai_key)
        env_content["OPENAI_API_KEY"] = request.openai_key
        configured.append("openai")

    # Save project path
    if request.project_path:
        env_content["LLM_DEBATE_PROJECT_PATH"] = request.project_path
        configured.append("project_path")

    # Save to .env file for persistence
    if configured:
        env_lines = [f"{k}={v}" for k, v in env_content.items()]
        env_file.write_text("\n".join(env_lines) + "\n")

    return {
        "status": "configured",
        "providers": [p for p in configured if p != "project_path"],
        "project_path": env_content.get("LLM_DEBATE_PROJECT_PATH"),
        "message": f"Configuration saved to .env",
    }


@app.get("/api/status")
async def get_status():
    """Check which providers are configured and return saved settings."""
    global debate_engine

    # Load saved config from .env
    env_file = Path(__file__).parent / ".env"
    saved_project_path = None
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("LLM_DEBATE_PROJECT_PATH="):
                saved_project_path = line.split("=", 1)[1].strip()
                break

    if not debate_engine:
        return {"configured": False, "providers": [], "project_path": saved_project_path}

    providers = []
    if debate_engine.anthropic_client:
        providers.append("anthropic")
    if debate_engine.openai_client:
        providers.append("openai")

    return {
        "configured": len(providers) > 0,
        "providers": providers,
        "project_path": saved_project_path,
        "active_sessions": len(debate_engine.sessions),
    }


@app.post("/api/debate/start")
async def start_debate(request: StartDebateRequest):
    """Start a new debate session (returns session_id for WebSocket connection)."""
    global debate_engine

    if not debate_engine:
        raise HTTPException(status_code=500, detail="Debate engine not initialized")

    # Validate providers
    if request.proposer_provider == "anthropic" and not debate_engine.anthropic_client:
        raise HTTPException(status_code=400, detail="Anthropic API key not configured")
    if request.proposer_provider == "openai" and not debate_engine.openai_client:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured")
    if request.critic_provider == "anthropic" and not debate_engine.anthropic_client:
        raise HTTPException(status_code=400, detail="Anthropic API key not configured")
    if request.critic_provider == "openai" and not debate_engine.openai_client:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured")

    # Validate project path
    if not Path(request.project_path).exists():
        raise HTTPException(status_code=400, detail=f"Project path not found: {request.project_path}")

    session_id = str(uuid.uuid4())[:8]

    return {
        "session_id": session_id,
        "task": request.task,
        "project_path": request.project_path,
        "proposer": request.proposer_provider,
        "critic": request.critic_provider,
        "max_rounds": request.max_rounds,
        "websocket_url": f"/ws/debate/{session_id}",
        "message": "Connect to WebSocket to start the debate",
    }


@app.get("/api/debate/{session_id}")
async def get_session(session_id: str):
    """Get debate session status and summary."""
    global debate_engine

    if not debate_engine:
        raise HTTPException(status_code=500, detail="Debate engine not initialized")

    summary = debate_engine.get_session_summary(session_id)
    if "error" in summary:
        raise HTTPException(status_code=404, detail=summary["error"])

    return summary


@app.get("/api/debate/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get all messages from a debate session."""
    global debate_engine

    if not debate_engine:
        raise HTTPException(status_code=500, detail="Debate engine not initialized")

    session = debate_engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = []
    for msg in session.messages:
        messages.append({
            "role": msg.role.value,
            "provider": msg.provider,
            "model": msg.model,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "tokens_used": msg.tokens_used,
            "is_consensus": msg.is_consensus,
            "concerns": msg.concerns,
        })

    return {
        "session_id": session_id,
        "status": session.status.value,
        "messages": messages,
    }


@app.get("/api/projects/browse")
async def browse_projects(path: str = "/"):
    """Browse directories for project selection."""
    target = Path(path).expanduser()

    if not target.exists():
        return {"error": "Path not found", "path": str(target)}

    if target.is_file():
        return {"error": "Path is a file, not a directory", "path": str(target)}

    items = []
    try:
        for item in sorted(target.iterdir()):
            if item.name.startswith("."):
                continue
            items.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
            })
    except PermissionError:
        return {"error": "Permission denied", "path": str(target)}

    return {
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else None,
        "items": items[:100],  # Limit to 100 items
    }


# --- Apply Code Endpoint ---

def extract_code_and_file(proposal: str) -> tuple[Optional[str], Optional[str]]:
    """Extract code block and target file from final proposal."""
    # Extract file path
    file_match = re.search(r"## File[:\s]*([^\n]+)", proposal, re.IGNORECASE)
    if not file_match:
        file_match = re.search(r"File:\s*`?([^\n`]+)`?", proposal, re.IGNORECASE)

    file_path = file_match.group(1).strip() if file_match else None

    # Extract code block
    code_match = re.search(r"```(?:python|javascript|js|jsx|tsx)?\n(.*?)```", proposal, re.DOTALL)
    code = code_match.group(1).strip() if code_match else None

    return file_path, code


@app.post("/api/debate/{session_id}/apply")
async def apply_code(session_id: str, request: ApplyCodeRequest):
    """
    Apply the consensus code to the target file.
    If preview_only=True, returns diff without making changes.
    """
    global debate_engine

    if not debate_engine:
        raise HTTPException(status_code=500, detail="Debate engine not initialized")

    session = debate_engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status != DebateStatus.CONSENSUS:
        raise HTTPException(status_code=400, detail=f"No consensus reached. Status: {session.status.value}")

    # Get the final approved proposal (last proposer message before consensus)
    final_proposal = None
    for msg in reversed(session.messages):
        if msg.role == MessageRole.PROPOSER:
            final_proposal = msg.content
            break

    if not final_proposal:
        raise HTTPException(status_code=400, detail="No proposal found in session")

    # Extract file path and code
    relative_path, new_code = extract_code_and_file(final_proposal)

    if not relative_path:
        raise HTTPException(status_code=400, detail="No target file specified in proposal. Proposer must include '## File: path/to/file'")

    if not new_code:
        raise HTTPException(status_code=400, detail="No code block found in proposal")

    # Resolve full path
    project_path = Path(session.task.split("PROJECT:")[-1].strip().split("\n")[0]) if "PROJECT:" in session.task else None

    # Try to find the file
    possible_paths = [
        Path(relative_path),
        Path(session.messages[0].content).parent / relative_path if session.messages else None,
    ]

    # Add common project roots
    common_roots = [
        Path("/Users/bhoomidakshpc/Project_WebSocket/ClawWork_FyersN7/ClawWork"),
        Path("/Users/bhoomidakshpc/Project_WebSocket/ClawWork_FyersN7"),
    ]

    for root in common_roots:
        possible_paths.append(root / relative_path)

    target_file = None
    for p in possible_paths:
        if p and p.exists():
            target_file = p
            break

    if not target_file:
        # Create the file if it's a new file
        if not request.preview_only:
            target_file = common_roots[0] / relative_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(new_code)
            return {
                "status": "created",
                "file": str(target_file),
                "message": f"Created new file: {relative_path}",
            }
        else:
            return {
                "status": "preview",
                "file": relative_path,
                "is_new_file": True,
                "new_code": new_code[:2000],
                "message": "File does not exist. Will be created.",
            }

    # Read existing file
    old_code = target_file.read_text()

    # Generate diff
    diff = list(difflib.unified_diff(
        old_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
    ))
    diff_text = "".join(diff)

    # Validation step
    validation_result = None
    if request.validate_first or request.auto_apply:
        validator = get_validator(target_file.parent)
        validation_result = validator.dry_run_apply(
            file_path=target_file,
            new_code=new_code,
            run_tests=request.run_tests,
        )

    if request.preview_only:
        result = {
            "status": "preview",
            "file": str(target_file),
            "is_new_file": False,
            "diff": diff_text,
            "old_lines": len(old_code.splitlines()),
            "new_lines": len(new_code.splitlines()),
        }
        if validation_result:
            result["validation"] = validation_result
        return result

    # If auto_apply, check validation passed
    if request.auto_apply and validation_result:
        if not validation_result["safe_to_apply"]:
            return {
                "status": "validation_failed",
                "file": str(target_file),
                "diff": diff_text,
                "validation": validation_result,
                "message": "Auto-apply blocked: validation failed. " + "; ".join(validation_result["errors"]),
            }

    # Final validation check before applying
    if request.validate_first and validation_result and not validation_result["safe_to_apply"]:
        return {
            "status": "validation_failed",
            "file": str(target_file),
            "diff": diff_text,
            "validation": validation_result,
            "message": "Apply blocked: " + "; ".join(validation_result["errors"]),
        }

    # Apply changes
    target_file.write_text(new_code)

    return {
        "status": "applied",
        "file": str(target_file),
        "diff": diff_text,
        "validation": validation_result,
        "message": f"Code applied to {relative_path}",
    }


@app.get("/api/debate/{session_id}/final-code")
async def get_final_code(session_id: str):
    """Get the final consensus code from a session."""
    global debate_engine

    if not debate_engine:
        raise HTTPException(status_code=500, detail="Debate engine not initialized")

    session = debate_engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the final proposal
    final_proposal = None
    for msg in reversed(session.messages):
        if msg.role == MessageRole.PROPOSER:
            final_proposal = msg.content
            break

    if not final_proposal:
        return {"error": "No proposal found"}

    file_path, code = extract_code_and_file(final_proposal)

    return {
        "session_id": session_id,
        "status": session.status.value,
        "has_consensus": session.status == DebateStatus.CONSENSUS,
        "target_file": file_path,
        "code": code,
        "full_proposal": final_proposal,
    }


# --- WebSocket Endpoint ---

@app.websocket("/ws/debate/{session_id}")
async def websocket_debate(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time debate streaming.

    Client sends initial config:
    {
        "action": "start",
        "task": "...",
        "project_path": "...",
        "proposer_provider": "anthropic",
        "critic_provider": "openai",
        "max_rounds": 7
    }
    """
    global debate_engine

    await websocket.accept()

    if session_id not in active_connections:
        active_connections[session_id] = []
    active_connections[session_id].append(websocket)

    try:
        # Wait for start/resume message
        data = await websocket.receive_json()
        action = data.get("action", "")

        if action == "resume":
            # Resume an existing session
            switch_provider = data.get("switch_provider")

            # Send confirmation
            await websocket.send_json({
                "type": "session_resumed",
                "session_id": session_id,
                "switch_provider": switch_provider,
            })

            # Resume debate and stream messages
            async for message in debate_engine.resume_debate(
                session_id=session_id,
                switch_provider=switch_provider,
            ):
                msg_data = {
                    "type": "message",
                    "role": message.role.value,
                    "provider": message.provider,
                    "model": message.model,
                    "content": message.content,
                    "timestamp": message.timestamp,
                    "tokens_used": message.tokens_used,
                    "is_consensus": message.is_consensus,
                    "concerns": message.concerns,
                }

                for conn in active_connections.get(session_id, []):
                    try:
                        await conn.send_json(msg_data)
                    except Exception:
                        pass

            # Send completion
            session = debate_engine.get_session(session_id)
            await websocket.send_json({
                "type": "debate_complete",
                "session_id": session_id,
                "status": session.status.value if session else "unknown",
                "rounds": session.current_round if session else 0,
                "has_consensus": session.status == DebateStatus.CONSENSUS if session else False,
            })
            return

        elif action != "start":
            await websocket.send_json({"error": "Expected 'start' or 'resume' action"})
            return

        # Start a new debate
        task = data.get("task", "")
        project_path = data.get("project_path", "")
        proposer_provider = data.get("proposer_provider", "anthropic")
        critic_provider = data.get("critic_provider", "openai")
        max_rounds = data.get("max_rounds", 7)

        if not task or not project_path:
            await websocket.send_json({"error": "Missing task or project_path"})
            return

        # Send confirmation
        await websocket.send_json({
            "type": "session_started",
            "session_id": session_id,
            "task": task,
            "project_path": project_path,
        })

        # Run debate and stream messages
        async for message in debate_engine.run_debate(
            session_id=session_id,
            task=task,
            project_path=project_path,
            proposer_provider=proposer_provider,
            critic_provider=critic_provider,
            max_rounds=max_rounds,
        ):
            msg_data = {
                "type": "message",
                "role": message.role.value,
                "provider": message.provider,
                "model": message.model,
                "content": message.content,
                "timestamp": message.timestamp,
                "tokens_used": message.tokens_used,
                "is_consensus": message.is_consensus,
                "concerns": message.concerns,
            }

            # Broadcast to all connections for this session
            for conn in active_connections.get(session_id, []):
                try:
                    await conn.send_json(msg_data)
                except Exception:
                    pass

        # Send completion
        session = debate_engine.get_session(session_id)
        await websocket.send_json({
            "type": "debate_complete",
            "session_id": session_id,
            "status": session.status.value if session else "unknown",
            "rounds": session.current_round if session else 0,
            "has_consensus": session.status == DebateStatus.CONSENSUS if session else False,
        })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if session_id in active_connections:
            if websocket in active_connections[session_id]:
                active_connections[session_id].remove(websocket)
            if not active_connections[session_id]:
                del active_connections[session_id]


# --- Serve Frontend ---

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

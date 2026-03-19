"""
Direct LangChain tool wrappers for LiveBench (no MCP)

Core tools: decide_activity, submit_work, learn, get_status
Productivity tools: Imported from livebench.tools.productivity
"""

from langchain_core.tools import tool
from typing import Dict, Any, Union
import json
import os
from datetime import datetime

from livebench.utils.logger import get_logger
from livebench.trading.fyers_client import FyersClient, MarketDataClient
from livebench.trading.screener import run_screener


# Global state (will be set by agent)
_global_state = {}

# Track recalled topics in current session for effectiveness measurement
_session_recalled_topics = []


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _record_fyers_order_attempt(entry: Dict[str, Any]) -> None:
    """Persist FYERS order attempts for audit/debugging."""
    data_path = _global_state.get("data_path")
    signature = _global_state.get("signature")

    if not data_path:
        return

    # data_path is typically already agent-specific (e.g., .../agent_data/<signature>)
    trading_dir = os.path.join(data_path, "trading")
    if signature and not os.path.basename(os.path.normpath(data_path)) == signature:
        trading_dir = os.path.join(data_path, signature, "trading")

    os.makedirs(trading_dir, exist_ok=True)
    log_file = os.path.join(trading_dir, "fyers_orders.jsonl")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _record_fyers_screener_run(entry: Dict[str, Any]) -> None:
    """Persist FYERS screener runs for audit/debugging."""
    data_path = _global_state.get("data_path")
    signature = _global_state.get("signature")

    if not data_path:
        return

    trading_dir = os.path.join(data_path, "trading")
    if signature and not os.path.basename(os.path.normpath(data_path)) == signature:
        trading_dir = os.path.join(data_path, signature, "trading")

    os.makedirs(trading_dir, exist_ok=True)
    log_file = os.path.join(trading_dir, "fyers_screener.jsonl")

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def set_global_state(
    signature: str,
    economic_tracker: Any,
    task_manager: Any,
    evaluator: Any,
    current_date: str,
    current_task: Dict,
    data_path: str,
    supports_multimodal: bool = True,
    knowledge_effectiveness_tracker: Any = None
):
    """Set global state for tools"""
    global _global_state
    _global_state = {
        "signature": signature,
        "economic_tracker": economic_tracker,
        "task_manager": task_manager,
        "evaluator": evaluator,
        "knowledge_effectiveness_tracker": knowledge_effectiveness_tracker,
        "current_date": current_date,
        "current_task": current_task,
        "data_path": data_path,
        "supports_multimodal": supports_multimodal
    }


@tool
def decide_activity(activity: str, reasoning: str) -> Dict[str, Any]:
    """
    Decide your daily activity: work or learn.

    Args:
        activity: Must be "work" or "learn"
        reasoning: Explanation for your decision (at least 50 characters)

    Returns:
        Dictionary with decision result
    """
    activity = activity.lower().strip()

    if activity not in ["work", "learn"]:
        return {
            "error": "Invalid activity. Must be 'work' or 'learn'",
            "valid_options": ["work", "learn"]
        }

    if len(reasoning) < 50:
        return {
            "error": "Reasoning must be at least 50 characters",
            "current_length": len(reasoning)
        }

    return {
        "success": True,
        "activity": activity,
        "reasoning": reasoning,
        "message": f"✅ Decision made: {activity.upper()}"
    }


@tool
def submit_work(work_output: str = "", artifact_file_paths: Union[list, str, None] = None) -> Dict[str, Any]:
    """
    Submit completed work for evaluation and payment.

    Args:
        work_output: Your completed work as text (detailed answer to the task). 
                     Minimum 100 characters if no artifact_file_paths provided.
        artifact_file_paths: Optional list of file paths to artifacts you created
                            (e.g., Excel files, PDFs, Python scripts). Use absolute paths.
                            Example: ["/path/to/report.xlsx", "/path/to/analysis.py"]

    Returns:
        Dictionary with evaluation result and payment
        
    Examples:
        # Submit text answer only
        submit_work(work_output="My detailed analysis of...")
        
        # Submit files only
        submit_work(artifact_file_paths=["/tmp/report.xlsx", "/tmp/analysis.py"])
        
        # Submit both text and files
        submit_work(
            work_output="Here is my analysis...",
            artifact_file_paths=["/tmp/report.xlsx"]
        )
    """
    logger = get_logger()
    
    # Normalize artifact_file_paths - handle both list and JSON string formats
    if artifact_file_paths is None:
        artifact_file_paths = []
    elif isinstance(artifact_file_paths, str):
        # Handle JSON string representation of list
        try:
            parsed = json.loads(artifact_file_paths)
            if isinstance(parsed, list):
                artifact_file_paths = parsed
                if logger:
                    logger.info(
                        "Converted JSON string to list for artifact_file_paths",
                        context={"count": len(artifact_file_paths)},
                        print_console=False
                    )
            else:
                return {
                    "error": f"artifact_file_paths must be a list, got {type(parsed).__name__} after parsing JSON"
                }
        except json.JSONDecodeError as e:
            return {
                "error": f"artifact_file_paths is a string but not valid JSON: {str(e)}"
            }
    
    # Validate input - must have either work_output or artifact_file_paths
    if not work_output and not artifact_file_paths:
        if logger:
            logger.warning(
                "No work submitted",
                context={"has_output": bool(work_output), "has_files": bool(artifact_file_paths)},
                print_console=False
            )
        return {
            "error": "Must provide either work_output (text) or artifact_file_paths (files), or both"
        }
    
    # Validate work_output length if no files provided
    if work_output and not artifact_file_paths and len(work_output) < 100:
        if logger:
            logger.warning(
                "Work output too short and no files provided",
                context={"length": len(work_output), "required": 100},
                print_console=False
            )
        return {
            "error": "Work output too short. Minimum 100 characters required when not submitting files.",
            "current_length": len(work_output)
        }

    # Get global state
    evaluator = _global_state.get("evaluator")
    task = _global_state.get("current_task")
    date = _global_state.get("current_date")
    signature = _global_state.get("signature")
    economic_tracker = _global_state.get("economic_tracker")
    data_path = _global_state.get("data_path")

    if not task:
        # Log detailed debug info about global state
        if logger:
            logger.error(
                "No task assigned - global state issue",
                context={
                    "has_evaluator": evaluator is not None,
                    "has_date": date is not None,
                    "has_signature": signature is not None,
                    "has_tracker": economic_tracker is not None,
                    "has_data_path": data_path is not None,
                    "current_date": date,
                    "signature": signature,
                    "global_state_keys": list(_global_state.keys())
                },
                print_console=True
            )
        return {"error": "No task assigned for today"}

    # Prepare artifact paths list
    all_artifact_paths = []
    
    # Save work_output to file if provided
    if work_output:
        work_dir = os.path.join(data_path, "work")
        os.makedirs(work_dir, exist_ok=True)

        # Create text artifact file
        text_artifact_path = os.path.join(work_dir, f"{date}_{task['task_id']}.txt")

        with open(text_artifact_path, "w", encoding="utf-8") as f:
            f.write(work_output)
        
        all_artifact_paths.append(text_artifact_path)
        
        if logger:
            logger.info(
                "Text work artifact saved",
                context={"path": text_artifact_path, "length": len(work_output)},
                print_console=False
            )
    
    # Add provided file paths
    if artifact_file_paths:
        # Verify files exist
        existing_files = []
        missing_files = []
        
        for file_path in artifact_file_paths:
            if os.path.exists(file_path):
                existing_files.append(file_path)
            else:
                missing_files.append(file_path)
        
        if missing_files:
            error_msg = f"Some artifact files not found: {missing_files}"
            if logger:
                logger.error(
                    "Artifact files missing",
                    context={"missing": missing_files, "existing": existing_files},
                    print_console=True
                )
            return {
                "error": error_msg,
                "missing_files": missing_files,
                "existing_files": existing_files
            }
        
        all_artifact_paths.extend(existing_files)
        
        if logger:
            logger.info(
                "File artifacts added",
                context={
                    "count": len(existing_files),
                    "files": [os.path.basename(f) for f in existing_files]
                },
                print_console=False
            )
    
    # Log submission
    if logger:
        logger.info(
            "Submitting work for evaluation",
            context={
                "task_id": task['task_id'],
                "total_artifacts": len(all_artifact_paths),
                "artifact_types": [os.path.splitext(f)[1] for f in all_artifact_paths]
            },
            print_console=False
        )
    
    # Build submission summary for agent feedback
    submission_summary = []
    submission_summary.append(f"📦 WORK SUBMISSION SUMMARY:")
    submission_summary.append(f"   Total artifacts: {len(all_artifact_paths)}")
    for i, path in enumerate(all_artifact_paths, 1):
        file_type = os.path.splitext(path)[1] or "text"
        file_size = os.path.getsize(path) if os.path.exists(path) else 0
        submission_summary.append(f"   {i}. {os.path.basename(path)} ({file_type}, {file_size} bytes)")

    # Evaluate work with all artifacts
    accepted, payment, feedback, evaluation_score = evaluator.evaluate_artifact(
        signature=signature,
        task=task,
        artifact_path=all_artifact_paths,  # Pass list of all artifacts
        description=f"Work submission with {len(all_artifact_paths)} artifact(s)"
    )

    # Record payment with evaluation score threshold (applies cliff at 0.6)
    actual_payment = economic_tracker.add_work_income(
        amount=payment,
        task_id=task["task_id"],
        evaluation_score=evaluation_score
    )

    # Record knowledge effectiveness if topics were recalled during this task
    global _session_recalled_topics
    knowledge_effectiveness_tracker = _global_state.get("knowledge_effectiveness_tracker")
    
    if knowledge_effectiveness_tracker and _session_recalled_topics:
        knowledge_effectiveness_tracker.record_task_completion(
            task_id=task["task_id"],
            evaluation_score=evaluation_score,
            payment=actual_payment,
            recalled_topics=_session_recalled_topics,
            baseline_score=0.5,  # Typical score without knowledge
            date=date
        )
        _session_recalled_topics = []  # Reset for next task
    
    result = {
        "accepted": accepted,
        "payment": payment,  # Raw payment from evaluator
        "actual_payment": actual_payment,  # Cliff-adjusted payment (respects 0.6 threshold)
        "feedback": feedback,
        "evaluation_score": evaluation_score,
        "artifact_paths": all_artifact_paths,  # Return list of all artifacts
        "submission_summary": "\n".join(submission_summary)
    }

    if actual_payment > 0:
        result["success"] = True

    return result


@tool
def learn(topic: str, knowledge: str) -> Dict[str, Any]:
    """
    Learn something new and add it to your knowledge base.

    Args:
        topic: Topic or title of what you learned
        knowledge: Detailed knowledge content (at least 200 characters)

    Returns:
        Dictionary with learning result
    """
    if len(knowledge) < 200:
        return {
            "error": "Knowledge content too short. Minimum 200 characters required.",
            "current_length": len(knowledge)
        }

    signature = _global_state.get("signature")
    date = _global_state.get("current_date")
    data_path = _global_state.get("data_path")

    # Save to learning memory
    memory_dir = os.path.join(data_path, "memory")
    os.makedirs(memory_dir, exist_ok=True)

    memory_file = os.path.join(memory_dir, "memory.jsonl")

    entry = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "topic": topic,
        "knowledge": knowledge
    }

    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "success": True,
        "topic": topic,
        "knowledge_length": len(knowledge),
        "message": f"✅ Learned about: {topic}"
    }


@tool
def recall_learning(query: str = "") -> Dict[str, Any]:
    """
    Recall knowledge from your learning memory.

    Args:
        query: Optional search term to filter topics (case-insensitive).
               If empty, returns all learned topics.

    Returns:
        Dictionary with matching learning entries
    """
    data_path = _global_state.get("data_path")

    if not data_path:
        return {"error": "Data path not available", "entries": []}

    memory_file = os.path.join(data_path, "memory", "memory.jsonl")

    if not os.path.exists(memory_file):
        return {
            "message": "No learning memory found. Use learn() to add knowledge.",
            "entries": [],
            "total_count": 0
        }

    # Read all entries
    entries = []
    with open(memory_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

    # Filter by query if provided
    if query.strip():
        query_lower = query.lower()
        filtered = [
            e for e in entries
            if query_lower in e.get("topic", "").lower()
            or query_lower in e.get("knowledge", "").lower()
        ]
    else:
        filtered = entries

    # Format for output with effectiveness metrics
    results = []
    effectiveness_tracker = _global_state.get("knowledge_effectiveness_tracker")
    
    for entry in filtered:
        topic = entry.get("topic", "Unknown")
        result_entry = {
            "topic": topic,
            "date": entry.get("date", ""),
            "knowledge": entry.get("knowledge", "")[:500] + ("..." if len(entry.get("knowledge", "")) > 500 else "")
        }
        
        # Add effectiveness metrics if tracker available
        if effectiveness_tracker:
            effectiveness = effectiveness_tracker.get_knowledge_effectiveness(topic)
            if effectiveness.get("total_uses", 0) > 0:
                result_entry["effectiveness"] = {
                    "total_uses": effectiveness["total_uses"],
                    "success_rate": effectiveness["success_rate"],
                    "avg_earnings_per_use": effectiveness["avg_earnings_per_use"],
                    "effective": effectiveness["effective"],
                    "last_used": effectiveness["last_used"]
                }
        
        results.append(result_entry)

    # Sort by effectiveness if available
    results = sorted(
        results,
        key=lambda x: (
            x.get("effectiveness", {}).get("total_uses", 0),
            x.get("effectiveness", {}).get("avg_earnings_per_use", 0)
        ),
        reverse=True
    )

    # Track recalled topics for effectiveness measurement
    global _session_recalled_topics
    for result in results:
        topic = result.get("topic")
        if topic and topic not in _session_recalled_topics:
            _session_recalled_topics.append(topic)

    return {
        "entries": results,
        "matched_count": len(results),
        "total_count": len(entries),
        "query": query if query else "(all topics)"
    }


@tool
def get_status() -> Dict[str, Any]:
    """
    Get your current economic status and balance.

    Returns:
        Dictionary with current status information
    """
    tracker = _global_state.get("economic_tracker")

    if not tracker:
        return {"error": "Economic tracker not available"}

    return {
        "balance": tracker.get_balance(),
        "net_worth": tracker.get_net_worth(),
        "daily_cost": tracker.get_daily_cost(),
        "status": tracker.get_survival_status()
    }


@tool
def get_learning_roi() -> Dict[str, Any]:
    """
    Get ROI (Return on Investment) metrics for your learning.
    Shows which knowledge contributes most to earnings.

    Returns:
        Dictionary with learning effectiveness and ROI metrics
    """
    effectiveness_tracker = _global_state.get("knowledge_effectiveness_tracker")
    
    if not effectiveness_tracker:
        return {"error": "Knowledge effectiveness tracker not available"}
    
    summary = effectiveness_tracker.get_learning_roi_summary()
    
    if summary.get("total_knowledge_items", 0) == 0:
        return {
            "message": "No learning effectiveness data yet. Start learning and applying knowledge!",
            "recommendation": "Use learn() to store knowledge, then recall it with recall_learning() when working on tasks."
        }
    
    high_roi_count = len(summary.get("high_roi_topics", []))
    total_earnings = round(summary["total_earnings_from_knowledge"], 2)
    
    return {
        "total_knowledge_items": summary["total_knowledge_items"],
        "total_knowledge_uses": summary["total_uses"],
        "total_earnings_from_knowledge": total_earnings,
        "avg_earnings_per_knowledge_use": round(summary["avg_earnings_per_use"], 2),
        "high_roi_topics": summary["high_roi_topics"],
        "recommendation": f"Your {high_roi_count} high-ROI topics have generated ${total_earnings:.2f}"
    }


@tool
def fyers_profile() -> Dict[str, Any]:
    """Fetch FYERS account profile using FYERS_ACCESS_TOKEN from environment."""
    client = FyersClient()
    return client.profile()


@tool
def fyers_funds() -> Dict[str, Any]:
    """Fetch FYERS funds and margin details."""
    client = FyersClient()
    return client.funds()


@tool
def fyers_holdings() -> Dict[str, Any]:
    """Fetch FYERS holdings."""
    client = FyersClient()
    return client.holdings()


@tool
def fyers_positions() -> Dict[str, Any]:
    """Fetch FYERS open and day positions."""
    client = FyersClient()
    return client.positions()


@tool
def fyers_quotes(symbols: str) -> Dict[str, Any]:
    """
    Fetch quote data for one or more symbols.

    Args:
        symbols: Comma-separated FYERS symbols, e.g. "NSE:SBIN-EQ,NSE:RELIANCE-EQ"
    """
    if not symbols or not symbols.strip():
        return {"success": False, "error": "symbols is required"}

    client = MarketDataClient(fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")))
    return client.quotes(symbols=symbols.strip())


@tool
def fyers_place_order(order_payload: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Place an order via FYERS /orders endpoint.

    Args:
        order_payload: JSON object (or JSON string) matching FYERS order schema.
    """
    if isinstance(order_payload, str):
        try:
            order_payload = json.loads(order_payload)
        except json.JSONDecodeError as exc:
            return {
                "success": False,
                "error": f"order_payload must be valid JSON: {exc}"
            }

    if not isinstance(order_payload, dict):
        return {
            "success": False,
            "error": "order_payload must be a JSON object"
        }

    dry_run = _env_flag("FYERS_DRY_RUN", True)
    allow_live_orders = _env_flag("FYERS_ALLOW_LIVE_ORDERS", False)

    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "signature": _global_state.get("signature"),
        "date": _global_state.get("current_date"),
        "dry_run": dry_run,
        "allow_live_orders": allow_live_orders,
        "order_payload": order_payload,
    }

    # Safety-first behavior: default dry-run, and explicit live permission required.
    if dry_run or not allow_live_orders:
        audit_entry["result"] = "blocked_dry_run"
        _record_fyers_order_attempt(audit_entry)
        return {
            "success": True,
            "dry_run": True,
            "order_sent": False,
            "reason": "Live order blocked by safety settings",
            "required_to_go_live": {
                "FYERS_DRY_RUN": "false",
                "FYERS_ALLOW_LIVE_ORDERS": "true"
            },
            "preview_order_payload": order_payload,
            "message": "DRY RUN: order not sent to FYERS"
        }

    client = FyersClient()
    result = client.place_order(order_payload=order_payload)

    audit_entry["result"] = "live_sent"
    audit_entry["response"] = {
        "success": result.get("success"),
        "status_code": result.get("status_code"),
        "error": result.get("error"),
    }
    _record_fyers_order_attempt(audit_entry)
    return result


@tool
def fyers_run_screener(watchlist: Union[str, list, None] = None) -> Dict[str, Any]:
    """
    Run beginner-friendly watchlist screener and produce dry-run order previews.

    Args:
        watchlist: Optional comma-separated symbols or JSON list.
                  If omitted, uses the default SENSEX, NIFTY50, and BANKNIFTY
                  watchlists from shared index config, with optional
                  FYERS_WATCHLIST_<INDEX> env overrides.
                  Example: "NSE:RELIANCE-EQ,NSE:TCS-EQ,NSE:HDFCBANK-EQ"
    """
    client = MarketDataClient(fallback_to_local=bool(os.getenv("FYERS_ACCESS_TOKEN")))
    result = run_screener(client=client, watchlist=watchlist)

    shadow_result: Dict[str, Any]
    if result.get("success"):
        try:
            from institutional_agents.integration.shadow_adapter import run_shadow_adapter

            shadow_result = run_shadow_adapter(
                baseline_result=result,
                runtime_context={
                    "signature": _global_state.get("signature"),
                    "current_date": _global_state.get("current_date"),
                    "data_path": _global_state.get("data_path"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            shadow_result = {
                "status": "failed_safe",
                "enabled": False,
                "shadow_mode": True,
                "record_count": 0,
                "error": str(exc),
            }
    else:
        shadow_result = {
            "status": "skipped_baseline_failed",
            "enabled": False,
            "shadow_mode": True,
            "record_count": 0,
        }

    result["institutional_shadow"] = shadow_result

    audit_entry = {
        "timestamp": datetime.now().isoformat(),
        "signature": _global_state.get("signature"),
        "date": _global_state.get("current_date"),
        "watchlist_input": watchlist,
        "success": result.get("success"),
        "summary": result.get("summary"),
        "institutional_shadow": {
            "status": shadow_result.get("status"),
            "record_count": shadow_result.get("record_count", 0),
            "agree_count": shadow_result.get("agree_count", 0),
            "disagree_count": shadow_result.get("disagree_count", 0),
        },
        "message": result.get("message"),
        "results": result.get("results"),
    }
    _record_fyers_screener_run(audit_entry)
    return result


# Import productivity tools from separate modules (if available)
try:
    from livebench.tools.productivity import (
        search_web as _search_web_original,
        create_file,
        execute_code_sandbox,
        read_file,
        create_video,
        read_webpage as _read_webpage_original
    )
    PRODUCTIVITY_TOOLS_AVAILABLE = True
except ImportError:
    PRODUCTIVITY_TOOLS_AVAILABLE = False
    print("⚠️ Productivity tools not available (livebench.tools.productivity not found)")


# Wrap search_web to track API costs (Tavily or Jina)
@tool
def search_web(query: str, max_results: int = 5, provider: str = None) -> Dict[str, Any]:
    """
    Search the internet for information using Tavily (default, with AI-generated answers) or Jina AI.

    Tavily provides structured results with AI-generated answers and relevance scores.
    Jina provides markdown-based results with titles, URLs, and snippets.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default: 5)
        provider: Search provider to use ("tavily" or "jina"). If not specified,
                 uses WEB_SEARCH_PROVIDER env var (defaults to "tavily")

    Returns:
        Dictionary with search results. Format depends on provider:

        Tavily: {"success": True, "provider": "tavily", "answer": "...", "results": [...], "images": [...]}
        Jina: {"success": True, "provider": "jina", "results": [...]}
    """
    if not PRODUCTIVITY_TOOLS_AVAILABLE:
        return {"error": "Search tool not available"}

    # Call original search_web with provider parameter
    result = _search_web_original.invoke({
        "query": query,
        "max_results": max_results,
        "provider": provider
    })

    # Track API cost if search was successful
    if isinstance(result, dict) and result.get("success"):
        try:
            tracker = _global_state.get("economic_tracker")
            if tracker:
                provider_used = result.get("provider", "unknown")

                if provider_used == "tavily":
                    # Tavily: Flat rate of $0.0008 per call
                    cost = tracker.track_flat_api_call(
                        cost=0.0008,
                        api_name="Tavily_Search"
                    )
                    result["api_cost"] = f"${cost:.6f}"
                    result["cost_type"] = "flat_rate"

                elif provider_used == "jina":
                    # Jina: Estimate tokens and charge at $0.05 per 1M tokens
                    result_text = str(result.get("results", ""))
                    estimated_tokens = len(result_text) // 4

                    cost = tracker.track_api_call(
                        tokens=estimated_tokens,
                        price_per_1m=0.05,
                        api_name="Jina_Search"
                    )
                    result["api_cost"] = f"${cost:.6f}"
                    result["estimated_tokens"] = estimated_tokens
                    result["cost_type"] = "per_token"

        except AttributeError as e:
            # Handle case where track_flat_api_call doesn't exist yet
            logger = get_logger()
            if logger:
                logger.warning(f"Economic tracker missing flat rate support, using fallback: {e}")

            # Fallback: Use track_api_call with fake tokens to achieve flat rate
            if result.get("provider") == "tavily":
                # $0.0008 per call = 16 tokens at $0.05 per 1M tokens
                fake_tokens = int(0.0008 * 1_000_000 / 0.05)
                cost = tracker.track_api_call(
                    tokens=fake_tokens,
                    price_per_1m=0.05,
                    api_name="Tavily_Search"
                )
                result["api_cost"] = f"${cost:.6f}"

        except Exception as e:
            # Don't fail the search if cost tracking fails
            logger = get_logger()
            if logger:
                logger.warning(f"Failed to track search API cost: {e}")

    return result


@tool
def read_webpage(urls: str, query: str = None) -> Dict[str, Any]:
    """Extract and read web page content from specified URLs using Tavily Extract.

    This tool extracts the main content from web pages, returning cleaned text
    in markdown format. Useful for reading articles, documentation, or any web content.

    Args:
        urls: Single URL or comma-separated list of URLs to extract content from
                 Example: "https://en.wikipedia.org/wiki/Artificial_intelligence"
        query: Optional query for reranking extracted content chunks based on relevance

    Returns:
        Dictionary with extracted web page content
    """
    if not PRODUCTIVITY_TOOLS_AVAILABLE:
        return {"error": "Webpage extraction tool not available"}

    # Call original read_webpage
    result = _read_webpage_original.invoke({
        "urls": urls,
        "query": query
    })

    # Track API cost if extraction was successful
    if isinstance(result, dict) and result.get("success"):
        try:
            tracker = _global_state.get("economic_tracker")
            if tracker:
                # Tavily Extract: Flat rate of $0.00016 per call (1 credit per 5 extractions)
                cost = tracker.track_flat_api_call(
                    cost=0.00016,
                    api_name="Tavily_Extract"
                )
                result["api_cost"] = f"${cost:.6f}"
                result["cost_type"] = "flat_rate"

        except AttributeError:
            # Fallback for older tracker versions
            logger = get_logger()
            if logger:
                logger.warning("Economic tracker missing flat rate support for webpage extraction")

            # Use track_api_call with fake tokens to achieve flat rate
            # $0.00016 per call = 3.2 tokens at $0.05 per 1M tokens
            fake_tokens = int(0.00016 * 1_000_000 / 0.05)
            cost = tracker.track_api_call(
                tokens=fake_tokens,
                price_per_1m=0.05,
                api_name="Tavily_Extract"
            )
            result["api_cost"] = f"${cost:.6f}"

        except Exception as e:
            logger = get_logger()
            if logger:
                logger.warning(f"Failed to track webpage extraction API cost: {e}")

    return result


def get_all_tools():
    """Get list of all LiveBench tools

    Returns:
    - 6 core tools (decide_activity, submit_work, learn, recall_learning, get_status, get_learning_roi)
    - 7 FYERS tools (profile, funds, holdings, positions, quotes, place_order, run_screener)
    - 6 productivity tools (search_web, read_webpage, create_file, execute_code_sandbox, read_file, create_video) if available
    """
    core_tools = [
        # Core tools
        decide_activity,
        submit_work,
        learn,
        recall_learning,
        get_status,
        get_learning_roi,
        fyers_profile,
        fyers_funds,
        fyers_holdings,
        fyers_positions,
        fyers_quotes,
        fyers_place_order,
        fyers_run_screener,
    ]

    if PRODUCTIVITY_TOOLS_AVAILABLE:
        productivity_tools = [
            search_web,
            read_webpage,
            create_file,
            execute_code_sandbox,
            read_file,
            create_video
        ]
        return core_tools + productivity_tools
    else:
        return core_tools

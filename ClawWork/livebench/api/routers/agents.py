"""Agent data endpoints (~10 routes)."""

import json
from datetime import datetime

from fastapi import APIRouter

from ..deps import (
    DATA_PATH,
    TASK_VALUES,
    _iter_real_agent_dirs,
    _load_decisions_from_activity_logs,
    _require_real_agent_dir,
)

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents")
async def get_agents():
    """Get list of all agents with their current status"""
    agents = []

    if not DATA_PATH.exists():
        return {"agents": []}

    for agent_dir in _iter_real_agent_dirs():
        signature = agent_dir.name

        # Get latest balance
        balance_file = agent_dir / "economic" / "balance.jsonl"
        balance_data = None
        if balance_file.exists():
            with open(balance_file, 'r') as f:
                lines = [line for line in f.readlines() if line.strip()]
                if lines:
                    balance_data = json.loads(lines[-1])

        # Get latest decision
        decision_file = agent_dir / "decisions" / "decisions.jsonl"
        current_activity = None
        current_date = None
        if decision_file.exists():
            with open(decision_file, 'r') as f:
                lines = [line for line in f.readlines() if line.strip()]
                if lines:
                    decision = json.loads(lines[-1])
                    current_activity = decision.get("activity")
                    current_date = decision.get("date")

        if balance_data:
            agents.append({
                "signature": signature,
                "balance": balance_data.get("balance", 0),
                "net_worth": balance_data.get("net_worth", 0),
                "survival_status": balance_data.get("survival_status", "unknown"),
                "current_activity": current_activity,
                "current_date": current_date,
                "total_token_cost": balance_data.get("total_token_cost", 0)
            })

    return {"agents": agents}


@router.get("/agents/{signature}")
async def get_agent_details(signature: str):
    """Get detailed information about a specific agent"""
    agent_dir = _require_real_agent_dir(signature)

    # Get balance history
    balance_file = agent_dir / "economic" / "balance.jsonl"
    balance_history = []
    if balance_file.exists():
        with open(balance_file, 'r') as f:
            for line in f:
                balance_history.append(json.loads(line))

    # Get decisions
    decision_file = agent_dir / "decisions" / "decisions.jsonl"
    decisions = []
    if decision_file.exists():
        with open(decision_file, 'r') as f:
            for line in f:
                decisions.append(json.loads(line))
    else:
        decisions = _load_decisions_from_activity_logs(agent_dir)

    # Get evaluation statistics
    evaluations_file = agent_dir / "work" / "evaluations.jsonl"
    avg_evaluation_score = None
    evaluation_scores = []

    if evaluations_file.exists():
        with open(evaluations_file, 'r') as f:
            for line in f:
                eval_data = json.loads(line)
                score = eval_data.get("evaluation_score")
                if score is not None:
                    evaluation_scores.append(score)

        if evaluation_scores:
            avg_evaluation_score = sum(evaluation_scores) / len(evaluation_scores)

    # Get latest status
    latest_balance = balance_history[-1] if balance_history else {}
    latest_decision = decisions[-1] if decisions else {}

    return {
        "signature": signature,
        "current_status": {
            "balance": latest_balance.get("balance", 0),
            "net_worth": latest_balance.get("net_worth", 0),
            "survival_status": latest_balance.get("survival_status", "unknown"),
            "total_token_cost": latest_balance.get("total_token_cost", 0),
            "total_work_income": latest_balance.get("total_work_income", 0),
            "current_activity": latest_decision.get("activity"),
            "current_date": latest_decision.get("date"),
            "avg_evaluation_score": avg_evaluation_score,
            "num_evaluations": len(evaluation_scores)
        },
        "balance_history": balance_history,
        "decisions": decisions,
        "evaluation_scores": evaluation_scores
    }


@router.get("/agents/{signature}/tasks")
async def get_agent_tasks(signature: str):
    """Get all tasks assigned to an agent"""
    agent_dir = _require_real_agent_dir(signature)

    tasks_file = agent_dir / "work" / "tasks.jsonl"
    evaluations_file = agent_dir / "work" / "evaluations.jsonl"

    tasks = []
    if tasks_file.exists():
        with open(tasks_file, 'r') as f:
            for line in f:
                tasks.append(json.loads(line))

    # Load evaluations grouped by task_id (preserve order)
    evaluations = {}
    if evaluations_file.exists():
        with open(evaluations_file, 'r') as f:
            for line in f:
                eval_data = json.loads(line)
                task_id = eval_data.get("task_id")
                if task_id:
                    if task_id not in evaluations:
                        evaluations[task_id] = []
                    evaluations[task_id].append(eval_data)

    # Merge tasks with evaluations
    for task in tasks:
        task_id = task.get("task_id")
        # Inject task market value if available
        if task_id and task_id in TASK_VALUES:
            task["task_value_usd"] = TASK_VALUES[task_id]
        evaluation_list = evaluations.get(task_id, [])
        evaluation = evaluation_list.pop(0) if evaluation_list else None
        if evaluation is not None:
            task["evaluation"] = evaluation
            task["completed"] = True
            task["payment"] = evaluation.get("payment", 0)
            task["feedback"] = evaluation.get("feedback", "")
            task["evaluation_score"] = evaluation.get("evaluation_score", None)
            task["evaluation_method"] = evaluation.get("evaluation_method", "heuristic")
        else:
            task["completed"] = False
            task["payment"] = 0
            task["evaluation_score"] = None

    return {"tasks": tasks}


@router.get("/agents/{signature}/terminal-log/{date}")
async def get_terminal_log(signature: str, date: str):
    """Get terminal log for an agent on a specific date"""
    from fastapi import HTTPException

    agent_dir = _require_real_agent_dir(signature)
    log_file = agent_dir / "terminal_logs" / f"{date}.log"
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    content = log_file.read_text(encoding="utf-8", errors="replace")
    return {"date": date, "content": content}


@router.get("/agents/{signature}/learning")
async def get_agent_learning(signature: str):
    """Get agent's learning memory"""
    agent_dir = _require_real_agent_dir(signature)

    memory_file = agent_dir / "memory" / "memory.jsonl"

    if not memory_file.exists():
        return {"memory": "", "entries": []}

    entries = []
    with open(memory_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                entries.append({
                    "topic": entry.get("topic", "Unknown"),
                    "timestamp": entry.get("timestamp", ""),
                    "date": entry.get("date", ""),
                    "content": entry.get("knowledge", "")
                })

    memory_content = "\n\n".join([
        f"## {entry['topic']} ({entry['date']})\n{entry['content']}"
        for entry in entries
    ])

    return {
        "memory": memory_content,
        "entries": entries
    }


@router.get("/agents/{signature}/economic")
async def get_agent_economic(signature: str):
    """Get economic metrics for an agent"""
    from fastapi import HTTPException

    agent_dir = _require_real_agent_dir(signature)

    balance_file = agent_dir / "economic" / "balance.jsonl"

    if not balance_file.exists():
        raise HTTPException(status_code=404, detail="No economic data found")

    dates = []
    balance_history = []
    token_costs = []
    work_income = []

    latest = {}
    with open(balance_file, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            latest = data
            dates.append(data.get("date", ""))
            balance_history.append(data.get("balance", 0))
            token_costs.append(data.get("daily_token_cost", 0))
            work_income.append(data.get("work_income_delta", 0))

    return {
        "balance": latest.get("balance", 0),
        "total_token_cost": latest.get("total_token_cost", 0),
        "total_work_income": latest.get("total_work_income", 0),
        "net_worth": latest.get("net_worth", 0),
        "survival_status": latest.get("survival_status", "unknown"),
        "dates": dates,
        "balance_history": balance_history,
        "token_costs": token_costs,
        "work_income": work_income
    }


@router.get("/agents/{signature}/learning/roi")
async def get_learning_roi(signature: str):
    """Get learning ROI metrics for an agent."""
    agent_dir = _require_real_agent_dir(signature)

    effectiveness_dir = agent_dir / "knowledge_effectiveness"
    index_file = effectiveness_dir / "knowledge_index.json"

    if not index_file.exists():
        return {
            "total_knowledge_items": 0,
            "total_knowledge_uses": 0,
            "total_earnings_from_knowledge": 0,
            "avg_earnings_per_use": 0,
            "high_roi_topics": [],
            "message": "No knowledge effectiveness data yet"
        }

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            index = json.load(f)

        total_items = len(index)
        total_uses = sum(data["total_uses"] for data in index.values())
        total_earnings = sum(data["total_earnings"] for data in index.values())
        avg_earnings_per_use = total_earnings / max(1, total_uses)

        high_roi_topics = []
        for topic, data in index.items():
            if data["total_uses"] >= 2:
                success_rate = data["successful_uses"] / max(1, data["total_uses"])
                avg_earnings = data["total_earnings"] / max(1, data["total_uses"])
                if success_rate >= 0.6 or avg_earnings >= 10.0:
                    high_roi_topics.append({
                        "topic": topic,
                        "total_uses": data["total_uses"],
                        "success_rate": round(success_rate, 2),
                        "total_earnings": round(data["total_earnings"], 2),
                        "avg_earnings": round(avg_earnings, 2),
                        "last_used": data["last_used"]
                    })

        high_roi_topics = sorted(high_roi_topics, key=lambda x: x["total_earnings"], reverse=True)

        return {
            "total_knowledge_items": total_items,
            "total_knowledge_uses": total_uses,
            "total_earnings_from_knowledge": round(total_earnings, 2),
            "avg_earnings_per_use": round(avg_earnings_per_use, 2),
            "high_roi_topics": high_roi_topics,
            "all_topics": [
                {
                    "topic": topic,
                    "total_uses": data["total_uses"],
                    "success_rate": round(data["successful_uses"] / max(1, data["total_uses"]), 2),
                    "total_earnings": round(data["total_earnings"], 2)
                }
                for topic, data in sorted(index.items(), key=lambda x: x[1]["total_earnings"], reverse=True)
            ]
        }
    except Exception as e:
        return {
            "error": str(e),
            "total_knowledge_items": 0,
            "high_roi_topics": []
        }


@router.get("/leaderboard")
async def get_leaderboard():
    """Get leaderboard data for all agents with summary metrics and balance histories"""
    if not DATA_PATH.exists():
        return {"agents": []}

    agents = []

    for agent_dir in _iter_real_agent_dirs():
        signature = agent_dir.name

        balance_file = agent_dir / "economic" / "balance.jsonl"
        balance_history = []
        if balance_file.exists():
            with open(balance_file, 'r') as f:
                for line in f:
                    if line.strip():
                        balance_history.append(json.loads(line))

        if not balance_history:
            continue

        latest = balance_history[-1]
        initial_balance = balance_history[0].get("balance", 0)
        current_balance = latest.get("balance", 0)
        pct_change = ((current_balance - initial_balance) / initial_balance * 100) if initial_balance else 0

        evaluations_file = agent_dir / "work" / "evaluations.jsonl"
        evaluation_scores = []
        if evaluations_file.exists():
            with open(evaluations_file, 'r') as f:
                for line in f:
                    if line.strip():
                        eval_data = json.loads(line)
                        score = eval_data.get("evaluation_score")
                        if score is not None:
                            evaluation_scores.append(score)

        avg_eval_score = (sum(evaluation_scores) / len(evaluation_scores)) if evaluation_scores else None

        stripped_history = [
            {
                "date": entry.get("date"),
                "balance": entry.get("balance", 0),
                "task_completion_time_seconds": entry.get("task_completion_time_seconds"),
            }
            for entry in balance_history
            if entry.get("date") != "initialization"
        ]

        agents.append({
            "signature": signature,
            "initial_balance": initial_balance,
            "current_balance": current_balance,
            "pct_change": round(pct_change, 1),
            "total_token_cost": latest.get("total_token_cost", 0),
            "total_work_income": latest.get("total_work_income", 0),
            "net_worth": latest.get("net_worth", 0),
            "survival_status": latest.get("survival_status", "unknown"),
            "num_tasks": len(evaluation_scores),
            "avg_eval_score": avg_eval_score,
            "balance_history": stripped_history,
        })

    agents.sort(key=lambda a: a["current_balance"], reverse=True)

    return {"agents": agents}


@router.get("/agents/{signature}/institutional-shadow/latest")
async def get_latest_institutional_shadow(signature: str):
    """Get latest institutional shadow summary from agent trading screener audit log."""
    agent_dir = _require_real_agent_dir(signature)

    screener_log = agent_dir / "trading" / "fyers_screener.jsonl"
    if not screener_log.exists():
        return {
            "available": False,
            "message": "No agent screener audit log found",
            "signature": signature,
        }

    latest_payload = None
    with open(screener_log, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                latest_payload = json.loads(line)
            except json.JSONDecodeError:
                continue

    if not isinstance(latest_payload, dict):
        return {
            "available": False,
            "message": "No valid screener audit entries found",
            "signature": signature,
        }

    shadow = latest_payload.get("institutional_shadow", {})
    return {
        "available": True,
        "signature": signature,
        "timestamp": latest_payload.get("timestamp"),
        "date": latest_payload.get("date"),
        "success": latest_payload.get("success"),
        "institutional_shadow": shadow if isinstance(shadow, dict) else {},
    }


@router.get("/agents/{signature}/dashboard-supplemental")
async def get_agent_dashboard_supplemental(signature: str):
    """Get the supplemental dashboard payload in a single request."""
    from ..routers.fyersn7 import get_latest_fyers_screener
    from ..routers.ensemble import get_market_session

    _require_real_agent_dir(signature)
    screener = await get_latest_fyers_screener()
    shadow = await get_latest_institutional_shadow(signature)
    market_session = await get_market_session()

    return {
        "signature": signature,
        "fyers_screener": screener,
        "institutional_shadow": shadow,
        "market_session": market_session,
        "updated_at": datetime.now().isoformat(),
    }

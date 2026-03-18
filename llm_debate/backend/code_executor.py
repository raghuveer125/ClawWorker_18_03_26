"""
Code Executor - Runs proposed code against real market data in a sandbox.
Validates claims by executing and comparing results.
"""

import re
import json
import traceback
from typing import Dict, List, Any, Optional
from io import StringIO
import sys

from data_provider import get_data_provider


class CodeExecutor:
    """Execute proposed trading code against real data safely."""

    def __init__(self):
        self.data_provider = get_data_provider()

    def extract_code_from_proposal(self, proposal: str) -> Optional[str]:
        """Extract code block from proposer's message."""
        # Look for ```python or ``` code blocks
        patterns = [
            r"```(?:python|javascript|js)?\n(.*?)```",
            r"## Code\n```\n(.*?)```",
        ]

        for pattern in patterns:
            match = re.search(pattern, proposal, re.DOTALL)
            if match:
                return match.group(1).strip()

        return None

    def extract_file_path(self, proposal: str) -> Optional[str]:
        """Extract target file path from proposal."""
        patterns = [
            r"## File[:\s]*([^\n]+)",
            r"File:\s*`?([^\n`]+)`?",
            r"Target:\s*`?([^\n`]+)`?",
        ]

        for pattern in patterns:
            match = re.search(pattern, proposal, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def prepare_test_data(self, symbol: str = "NSE:NIFTY50-INDEX") -> List[Dict]:
        """Get candle data formatted for testing."""
        result = self.data_provider.fetch_candles(symbol, "5m", days=5)

        if not result["success"]:
            return []

        return result["candles"]

    def execute_swing_detection(
        self,
        code: str,
        candles: List[Dict],
        function_name: str = "findSwingPoints",
    ) -> Dict[str, Any]:
        """
        Execute swing detection code and return results.

        Returns:
            {
                "success": bool,
                "swing_highs": int,
                "swing_lows": int,
                "execution_time_ms": float,
                "sample_points": [...],
                "error": str (if failed),
            }
        """
        # Convert Python-style code to executable
        # This is a simplified executor - in production, use a proper sandbox

        try:
            # Prepare data in the format the code expects
            data = []
            for c in candles:
                data.append({
                    "time": c["timestamp"] if "timestamp" in c else 0,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c.get("volume", 0),
                })

            # Create a namespace for execution
            namespace = {
                "data": data,
                "candles": data,
                "result": None,
                "swing_highs": [],
                "swing_lows": [],
            }

            # Add common math functions
            import math
            namespace["math"] = math
            namespace["abs"] = abs
            namespace["max"] = max
            namespace["min"] = min
            namespace["len"] = len
            namespace["range"] = range
            namespace["enumerate"] = enumerate

            # Adapt JavaScript-style code to Python (basic conversion)
            py_code = self._js_to_python(code)

            # Execute
            import time
            start = time.time()

            exec(py_code, namespace)

            # Try to find results
            swing_highs = namespace.get("swing_highs", namespace.get("swingHighs", []))
            swing_lows = namespace.get("swing_lows", namespace.get("swingLows", []))

            # If function was defined, call it
            if function_name in namespace or "find_swing_points" in namespace:
                func = namespace.get(function_name) or namespace.get("find_swing_points")
                if callable(func):
                    result = func(data)
                    if isinstance(result, dict):
                        swing_highs = result.get("swingHighs", result.get("swing_highs", []))
                        swing_lows = result.get("swingLows", result.get("swing_lows", []))
                    elif isinstance(result, tuple) and len(result) >= 2:
                        swing_highs, swing_lows = result[0], result[1]

            exec_time = (time.time() - start) * 1000

            return {
                "success": True,
                "swing_highs_count": len(swing_highs) if swing_highs else 0,
                "swing_lows_count": len(swing_lows) if swing_lows else 0,
                "total_candles": len(candles),
                "execution_time_ms": round(exec_time, 2),
                "sample_highs": swing_highs[:5] if swing_highs else [],
                "sample_lows": swing_lows[:5] if swing_lows else [],
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"{type(e).__name__}: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    def _js_to_python(self, code: str) -> str:
        """Basic JavaScript to Python conversion."""
        # This is a simplified converter - handles common patterns
        py = code

        # const/let/var -> nothing (Python doesn't need these)
        py = re.sub(r'\b(const|let|var)\s+', '', py)

        # function name(args) -> def name(args):
        py = re.sub(r'\bfunction\s+(\w+)\s*\(([^)]*)\)\s*\{', r'def \1(\2):', py)

        # Arrow functions (simple ones)
        py = re.sub(r'\(([^)]*)\)\s*=>\s*\{', r'def anonymous(\1):', py)

        # Remove braces (basic - won't handle nested well)
        py = py.replace('{', '').replace('}', '')

        # === to ==
        py = py.replace('===', '==').replace('!==', '!=')

        # .length to len()
        py = re.sub(r'(\w+)\.length', r'len(\1)', py)

        # .push(x) to .append(x)
        py = re.sub(r'\.push\(', '.append(', py)

        # for (let i = 0; i < n; i++) to for i in range(n):
        py = re.sub(
            r'for\s*\(\s*\w+\s*=\s*(\d+)\s*;\s*\w+\s*<\s*(\w+)\s*;\s*\w+\+\+\s*\)',
            r'for i in range(\1, \2):',
            py
        )

        # true/false to True/False
        py = re.sub(r'\btrue\b', 'True', py)
        py = re.sub(r'\bfalse\b', 'False', py)
        py = re.sub(r'\bnull\b', 'None', py)

        # Remove semicolons
        py = py.replace(';', '')

        return py

    def validate_proposal(
        self,
        proposal: str,
        symbol: str = "NSE:NIFTY50-INDEX",
    ) -> Dict[str, Any]:
        """
        Validate a proposal by extracting and executing its code.

        Returns validation results that can be shown to the LLMs.
        """
        code = self.extract_code_from_proposal(proposal)

        if not code:
            return {
                "validated": False,
                "reason": "No code block found in proposal",
            }

        candles = self.prepare_test_data(symbol)

        if not candles:
            return {
                "validated": False,
                "reason": "Could not fetch market data for validation",
            }

        result = self.execute_swing_detection(code, candles)

        if not result["success"]:
            return {
                "validated": False,
                "reason": f"Code execution failed: {result.get('error', 'Unknown error')}",
                "details": result,
            }

        # Add context about the data
        result["data_context"] = {
            "symbol": symbol,
            "candle_count": len(candles),
            "date_range": f"{candles[0]['time']} to {candles[-1]['time']}",
        }

        return {
            "validated": True,
            "results": result,
        }

    def format_validation_for_llm(self, validation: Dict) -> str:
        """Format validation results for LLM context."""
        if not validation.get("validated"):
            return f"VALIDATION FAILED: {validation.get('reason', 'Unknown')}"

        r = validation["results"]
        ctx = r.get("data_context", {})

        lines = [
            "VALIDATION RESULTS",
            "=" * 40,
            f"Data: {ctx.get('symbol', 'N/A')} ({ctx.get('candle_count', 0)} candles)",
            f"Range: {ctx.get('date_range', 'N/A')}",
            "",
            f"Swing Highs Detected: {r.get('swing_highs_count', 0)}",
            f"Swing Lows Detected: {r.get('swing_lows_count', 0)}",
            f"Execution Time: {r.get('execution_time_ms', 0)}ms",
        ]

        if r.get("sample_highs"):
            lines.append(f"Sample High Indices: {r['sample_highs']}")
        if r.get("sample_lows"):
            lines.append(f"Sample Low Indices: {r['sample_lows']}")

        return "\n".join(lines)


# Singleton
_executor = None

def get_code_executor() -> CodeExecutor:
    global _executor
    if _executor is None:
        _executor = CodeExecutor()
    return _executor

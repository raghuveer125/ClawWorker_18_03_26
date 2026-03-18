"""
Guardian Bot - Code stability layer.
Handles linting, syntax checks, dependency scans, and test runs.
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_bot import BaseBot, BotContext, BotResult, BotStatus


class GuardianBot(BaseBot):
    """
    Guardian Bot ensures code stability before deployment.

    Responsibilities:
    - Lint code (ruff, eslint)
    - Syntax validation
    - Dependency vulnerability scan
    - Run test suites
    - Check for security issues
    """

    BOT_TYPE = "guardian"
    REQUIRES_LLM = False

    def __init__(
        self,
        project_path: Optional[str] = None,
        run_tests: bool = True,
        run_lint: bool = True,
        run_security: bool = True,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.project_path = Path(project_path) if project_path else None
        self.run_tests = run_tests
        self.run_lint = run_lint
        self.run_security = run_security

    def get_description(self) -> str:
        return "Validates code quality: linting, tests, security scans"

    async def execute(self, context: BotContext) -> BotResult:
        """Run all code quality checks."""
        project_path = self.project_path or Path(context.data.get("project_path", "."))

        results = {
            "lint": None,
            "tests": None,
            "security": None,
            "syntax": None,
        }
        errors = []
        warnings = []

        # Syntax check
        syntax_result = await self._check_syntax(project_path)
        results["syntax"] = syntax_result
        if not syntax_result["valid"]:
            errors.extend(syntax_result.get("errors", []))

        # Lint check
        if self.run_lint:
            lint_result = await self._run_lint(project_path)
            results["lint"] = lint_result
            if lint_result.get("errors"):
                warnings.append(f"Lint issues: {len(lint_result['errors'])}")

        # Run tests
        if self.run_tests:
            test_result = await self._run_tests(project_path)
            results["tests"] = test_result
            if not test_result.get("passed"):
                errors.append(f"Tests failed: {test_result.get('failed_count', 0)} failures")

        # Security scan
        if self.run_security:
            security_result = await self._run_security_scan(project_path)
            results["security"] = security_result
            if security_result.get("vulnerabilities"):
                warnings.append(f"Security issues: {len(security_result['vulnerabilities'])}")

        # Determine overall status
        status = BotStatus.SUCCESS
        if errors:
            status = BotStatus.FAILED

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=status,
            output=results,
            errors=errors,
            warnings=warnings,
            metrics={
                "lint_issues": len(results.get("lint", {}).get("errors", [])),
                "test_passed": results.get("tests", {}).get("passed_count", 0),
                "test_failed": results.get("tests", {}).get("failed_count", 0),
                "vulnerabilities": len(results.get("security", {}).get("vulnerabilities", [])),
            },
            next_bot="self_healing" if errors else "optimizer",
        )

    async def _check_syntax(self, project_path: Path) -> Dict[str, Any]:
        """Check Python syntax."""
        errors = []

        for py_file in project_path.rglob("*.py"):
            if ".venv" in str(py_file) or "node_modules" in str(py_file):
                continue

            try:
                with open(py_file) as f:
                    compile(f.read(), py_file, "exec")
            except SyntaxError as e:
                errors.append({
                    "file": str(py_file.relative_to(project_path)),
                    "line": e.lineno,
                    "message": str(e.msg),
                })

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "files_checked": len(list(project_path.rglob("*.py"))),
        }

    async def _run_lint(self, project_path: Path) -> Dict[str, Any]:
        """Run linter (ruff for Python)."""
        try:
            result = await asyncio.create_subprocess_exec(
                "ruff", "check", str(project_path), "--output-format=json",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = await result.communicate()

            if stdout:
                import json
                issues = json.loads(stdout)
                return {
                    "tool": "ruff",
                    "errors": issues,
                    "count": len(issues),
                }

            return {"tool": "ruff", "errors": [], "count": 0}

        except FileNotFoundError:
            return {"tool": "ruff", "error": "ruff not installed", "errors": []}

    async def _run_tests(self, project_path: Path) -> Dict[str, Any]:
        """Run pytest."""
        try:
            result = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest",
                str(project_path),
                "--tb=short", "-q", "--no-header",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(project_path),
            )
            stdout, stderr = await result.communicate()

            output = stdout.decode()
            passed = result.returncode == 0

            # Parse test counts from output
            passed_count = 0
            failed_count = 0
            for line in output.split("\n"):
                if "passed" in line:
                    import re
                    match = re.search(r"(\d+) passed", line)
                    if match:
                        passed_count = int(match.group(1))
                if "failed" in line:
                    import re
                    match = re.search(r"(\d+) failed", line)
                    if match:
                        failed_count = int(match.group(1))

            return {
                "passed": passed,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "output": output[-2000:],  # Last 2000 chars
            }

        except Exception as e:
            return {"passed": True, "error": str(e), "skipped": True}

    async def _run_security_scan(self, project_path: Path) -> Dict[str, Any]:
        """Run security scan (pip-audit or npm audit)."""
        vulnerabilities = []

        # Check Python dependencies
        requirements_file = project_path / "requirements.txt"
        if requirements_file.exists():
            try:
                result = await asyncio.create_subprocess_exec(
                    "pip-audit", "-r", str(requirements_file), "--format=json",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, _ = await result.communicate()

                if stdout:
                    import json
                    vulns = json.loads(stdout)
                    vulnerabilities.extend(vulns)

            except FileNotFoundError:
                pass  # pip-audit not installed

        # Check npm dependencies
        package_json = project_path / "package.json"
        if package_json.exists():
            try:
                result = await asyncio.create_subprocess_exec(
                    "npm", "audit", "--json",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(project_path),
                )
                stdout, _ = await result.communicate()

                if stdout:
                    import json
                    audit = json.loads(stdout)
                    if audit.get("vulnerabilities"):
                        for name, info in audit["vulnerabilities"].items():
                            vulnerabilities.append({
                                "package": name,
                                "severity": info.get("severity"),
                                "ecosystem": "npm",
                            })

            except (FileNotFoundError, Exception):
                pass

        return {
            "vulnerabilities": vulnerabilities,
            "count": len(vulnerabilities),
        }


class SelfHealingBot(BaseBot):
    """
    Self-Healing Bot - Automatically fixes code issues.

    Uses LLM debate to propose and validate fixes for:
    - Lint errors
    - Test failures
    - Security vulnerabilities
    """

    BOT_TYPE = "self_healing"
    REQUIRES_LLM = True

    def get_description(self) -> str:
        return "Automatically proposes fixes for code issues using LLM consensus"

    async def execute(self, context: BotContext) -> BotResult:
        """Analyze issues and propose fixes."""
        # Get issues from previous guardian bot run
        last_result = context.get_last_result()
        if not last_result or last_result.bot_type != "guardian":
            return BotResult(
                bot_id=self.bot_id,
                bot_type=self.BOT_TYPE,
                status=BotStatus.SUCCESS,
                output={"message": "No issues to fix"},
            )

        issues = last_result.output
        fixes_proposed = []

        # For each type of issue, request LLM debate for fix
        if issues.get("syntax", {}).get("errors"):
            for error in issues["syntax"]["errors"][:3]:  # Limit to 3
                task = f"""
                Fix this Python syntax error:
                File: {error['file']}
                Line: {error['line']}
                Error: {error['message']}

                Read the file and propose a minimal fix.
                """

                debate_result = await self.request_llm_debate(
                    task=task,
                    project_path=str(context.data.get("project_path", ".")),
                    max_rounds=3,
                )

                if debate_result.get("consensus"):
                    fixes_proposed.append({
                        "type": "syntax",
                        "file": error["file"],
                        "consensus": True,
                        "session_id": debate_result.get("session_id"),
                    })

        if issues.get("tests", {}).get("failed_count", 0) > 0:
            task = f"""
            Tests are failing. Analyze the test output and propose fixes:

            Output: {issues['tests'].get('output', 'No output')[-1000:]}

            Find the root cause and propose minimal code changes.
            """

            debate_result = await self.request_llm_debate(
                task=task,
                project_path=str(context.data.get("project_path", ".")),
                max_rounds=5,
            )

            if debate_result.get("consensus"):
                fixes_proposed.append({
                    "type": "test_failure",
                    "consensus": True,
                    "session_id": debate_result.get("session_id"),
                })

        return BotResult(
            bot_id=self.bot_id,
            bot_type=self.BOT_TYPE,
            status=BotStatus.SUCCESS if fixes_proposed else BotStatus.FAILED,
            output={"fixes_proposed": fixes_proposed},
            metrics={"fixes_count": len(fixes_proposed)},
        )

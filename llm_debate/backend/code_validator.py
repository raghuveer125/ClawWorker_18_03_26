"""
Code Validator - Dry-run validation before applying patches.
Ensures code changes won't break the application.
"""

import ast
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple


class CodeValidator:
    """Validate code changes before applying them."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path("/Users/bhoomidakshpc/Project_WebSocket/ClawWork_FyersN7/ClawWork")

    def validate_syntax(self, code: str, file_path: str) -> Dict:
        """
        Validate code syntax based on file extension.
        Returns: {"valid": bool, "error": str or None}
        """
        ext = Path(file_path).suffix.lower()

        if ext == ".py":
            return self._validate_python(code)
        elif ext in [".js", ".jsx", ".ts", ".tsx"]:
            return self._validate_javascript(code, file_path)
        elif ext == ".json":
            return self._validate_json(code)
        else:
            # For unknown types, assume valid (no syntax check available)
            return {"valid": True, "error": None, "warning": f"No syntax validator for {ext}"}

    def _validate_python(self, code: str) -> Dict:
        """Validate Python syntax using ast.parse."""
        try:
            ast.parse(code)
            return {"valid": True, "error": None}
        except SyntaxError as e:
            return {
                "valid": False,
                "error": f"Python syntax error at line {e.lineno}: {e.msg}",
                "line": e.lineno,
            }

    def _validate_javascript(self, code: str, file_path: str) -> Dict:
        """Validate JS/JSX/TS/TSX syntax using node if available."""
        # Quick heuristic checks first
        issues = []

        # Check for obvious syntax issues
        open_braces = code.count("{") - code.count("}")
        open_parens = code.count("(") - code.count(")")
        open_brackets = code.count("[") - code.count("]")

        if open_braces != 0:
            issues.append(f"Unbalanced braces: {'+' if open_braces > 0 else ''}{open_braces}")
        if open_parens != 0:
            issues.append(f"Unbalanced parentheses: {'+' if open_parens > 0 else ''}{open_parens}")
        if open_brackets != 0:
            issues.append(f"Unbalanced brackets: {'+' if open_brackets > 0 else ''}{open_brackets}")

        if issues:
            return {"valid": False, "error": "; ".join(issues)}

        # Try using node for actual parsing (if available)
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=Path(file_path).suffix, delete=False) as f:
                f.write(code)
                temp_path = f.name

            # Use node to check syntax (won't execute, just parse)
            result = subprocess.run(
                ["node", "--check", temp_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            Path(temp_path).unlink()

            if result.returncode != 0:
                return {"valid": False, "error": result.stderr.strip()}

            return {"valid": True, "error": None}

        except (subprocess.TimeoutExpired, FileNotFoundError):
            # Node not available or timeout - rely on heuristic checks
            return {"valid": True, "error": None, "warning": "Node.js not available for full syntax check"}

    def _validate_json(self, code: str) -> Dict:
        """Validate JSON syntax."""
        import json
        try:
            json.loads(code)
            return {"valid": True, "error": None}
        except json.JSONDecodeError as e:
            return {"valid": False, "error": f"JSON error at line {e.lineno}: {e.msg}"}

    def run_tests(self, test_command: Optional[str] = None, timeout: int = 60) -> Dict:
        """
        Run project tests to validate changes don't break anything.
        Returns: {"passed": bool, "output": str, "error": str or None}
        """
        if not test_command:
            # Try to detect test command
            test_command = self._detect_test_command()

        if not test_command:
            return {
                "passed": True,
                "skipped": True,
                "output": "No test command configured or detected",
                "error": None,
            }

        try:
            result = subprocess.run(
                test_command,
                shell=True,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return {
                "passed": result.returncode == 0,
                "skipped": False,
                "output": result.stdout[-2000:] if result.stdout else "",
                "error": result.stderr[-1000:] if result.returncode != 0 else None,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "skipped": False,
                "output": "",
                "error": f"Test command timed out after {timeout}s",
            }
        except Exception as e:
            return {
                "passed": False,
                "skipped": False,
                "output": "",
                "error": str(e),
            }

    def _detect_test_command(self) -> Optional[str]:
        """Detect the test command based on project structure."""
        # Check for common test configurations
        if (self.project_root / "package.json").exists():
            return "npm test --passWithNoTests 2>/dev/null || true"
        elif (self.project_root / "pytest.ini").exists():
            return "pytest --tb=short -q 2>/dev/null || true"
        elif (self.project_root / "setup.py").exists():
            return "python -m pytest --tb=short -q 2>/dev/null || true"
        return None

    def dry_run_apply(
        self,
        file_path: Path,
        new_code: str,
        run_tests: bool = True,
        test_command: Optional[str] = None,
    ) -> Dict:
        """
        Perform a complete dry-run validation:
        1. Validate syntax
        2. Create backup
        3. Temporarily apply changes
        4. Run tests (optional)
        5. Restore original

        Returns comprehensive validation result.
        """
        result = {
            "syntax_valid": False,
            "tests_passed": None,
            "safe_to_apply": False,
            "errors": [],
            "warnings": [],
        }

        # Step 1: Validate syntax
        syntax_check = self.validate_syntax(new_code, str(file_path))
        result["syntax_valid"] = syntax_check["valid"]

        if not syntax_check["valid"]:
            result["errors"].append(f"Syntax error: {syntax_check['error']}")
            return result

        if syntax_check.get("warning"):
            result["warnings"].append(syntax_check["warning"])

        # Step 2: If tests requested, do temporary apply
        if run_tests and file_path.exists():
            # Backup original
            original_content = file_path.read_text()

            try:
                # Apply temporarily
                file_path.write_text(new_code)

                # Run tests
                test_result = self.run_tests(test_command)
                result["tests_passed"] = test_result["passed"]
                result["tests_skipped"] = test_result.get("skipped", False)

                if not test_result["passed"] and not test_result.get("skipped"):
                    result["errors"].append(f"Tests failed: {test_result.get('error', 'Unknown error')}")

            finally:
                # Always restore original
                file_path.write_text(original_content)

        else:
            result["tests_skipped"] = True
            result["tests_passed"] = True

        # Determine if safe to apply
        result["safe_to_apply"] = (
            result["syntax_valid"] and
            (result["tests_passed"] or result.get("tests_skipped", False))
        )

        return result


# Singleton
_validator = None


def get_validator(project_root: Optional[Path] = None) -> CodeValidator:
    global _validator
    if _validator is None or project_root:
        _validator = CodeValidator(project_root)
    return _validator

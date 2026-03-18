"""
Environment File Manager
Handles loading and saving credentials to .env files
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional, List

from .config import ENV_VARS, DEFAULT_ENV_FILES


class EnvManager:
    """Manages FYERS credentials in environment files."""

    def __init__(self, env_file: Optional[str] = None, project_root: Optional[str] = None):
        """
        Initialize EnvManager.

        Args:
            env_file: Explicit path to .env file. If None, searches DEFAULT_ENV_FILES.
            project_root: Project root directory. Defaults to current working directory.
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.env_file = self._resolve_env_file(env_file)

    def _resolve_env_file(self, env_file: Optional[str]) -> Path:
        """Find or create the .env file path."""
        if env_file:
            path = Path(env_file)
            if not path.is_absolute():
                path = self.project_root / path
            return path

        # Search cwd first, then walk parent directories for an existing env file.
        search_roots = [self.project_root] + list(self.project_root.parents)
        for base in search_roots:
            for name in DEFAULT_ENV_FILES:
                path = base / name
                if path.exists():
                    return path

        # Default to .env if none found
        return self.project_root / ".env"

    def load(self) -> Dict[str, str]:
        """
        Load credentials from env file.

        Returns:
            Dict with keys: client_id, secret_key, redirect_uri, access_token
        """
        raw = self._load_raw()

        # Normalize to standard keys
        result = {}

        # Client ID (check both names)
        result["client_id"] = (
            raw.get(ENV_VARS["client_id"]) or
            raw.get(ENV_VARS["app_id"]) or
            ""
        )

        # Secret Key (check both names)
        result["secret_key"] = (
            raw.get(ENV_VARS["secret_key"]) or
            raw.get(ENV_VARS["secret_id"]) or
            ""
        )

        # Redirect URI
        result["redirect_uri"] = raw.get(ENV_VARS["redirect_uri"]) or ""

        # Access Token
        result["access_token"] = raw.get(ENV_VARS["access_token"]) or ""

        return result

    def _load_raw(self) -> Dict[str, str]:
        """Load raw key-value pairs from env file."""
        result = {}
        if not self.env_file.exists():
            return result

        with open(self.env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                result[key] = value

        return result

    def save(
        self,
        client_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> None:
        """
        Save credentials to env file.
        Only updates provided values, preserves others.
        """
        # Load existing
        raw = self._load_raw() if self.env_file.exists() else {}

        # Update provided values (using standard env var names)
        if client_id is not None:
            raw[ENV_VARS["client_id"]] = client_id
            # Remove alias if exists to avoid duplication
            raw.pop(ENV_VARS["app_id"], None)

        if secret_key is not None:
            raw[ENV_VARS["secret_key"]] = secret_key
            raw.pop(ENV_VARS["secret_id"], None)

        if redirect_uri is not None:
            raw[ENV_VARS["redirect_uri"]] = redirect_uri

        if access_token is not None:
            raw[ENV_VARS["access_token"]] = access_token

        # Write back
        self._write(raw)

    def _write(self, values: Dict[str, str]) -> None:
        """Write values to env file in standard format."""
        # Ensure parent directory exists
        self.env_file.parent.mkdir(parents=True, exist_ok=True)

        # Standard order for FYERS credentials
        ordered_keys = [
            ENV_VARS["client_id"],
            ENV_VARS["secret_key"],
            ENV_VARS["redirect_uri"],
            ENV_VARS["access_token"],
        ]

        lines = ["# FYERS API Credentials (managed by shared/auth)"]

        # Write ordered keys first
        for key in ordered_keys:
            if key in values:
                lines.append(f"{key}={values[key]}")

        # Write any remaining keys (preserve other env vars)
        for key, value in values.items():
            if key not in ordered_keys:
                lines.append(f"{key}={value}")

        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def update_token(self, access_token: str) -> None:
        """Quick method to update just the access token."""
        self.save(access_token=access_token)

    def load_to_environ(self) -> None:
        """Load credentials into os.environ for use by other modules."""
        creds = self.load()

        if creds["client_id"]:
            os.environ[ENV_VARS["client_id"]] = creds["client_id"]
            os.environ[ENV_VARS["app_id"]] = creds["client_id"]  # Set alias too

        if creds["secret_key"]:
            os.environ[ENV_VARS["secret_key"]] = creds["secret_key"]
            os.environ[ENV_VARS["secret_id"]] = creds["secret_key"]

        if creds["redirect_uri"]:
            os.environ[ENV_VARS["redirect_uri"]] = creds["redirect_uri"]

        if creds["access_token"]:
            os.environ[ENV_VARS["access_token"]] = creds["access_token"]

    def get_env_file_path(self) -> str:
        """Return the resolved env file path."""
        return str(self.env_file)

    def has_valid_credentials(self) -> bool:
        """Check if we have minimum required credentials."""
        creds = self.load()
        return bool(creds["client_id"] and creds["access_token"])


def find_env_file(search_paths: Optional[List[str]] = None) -> Optional[Path]:
    """
    Search for .env file in multiple locations.

    Args:
        search_paths: List of directories to search. Defaults to cwd and parents.

    Returns:
        Path to first found .env file, or None.
    """
    if search_paths is None:
        cwd = Path.cwd()
        search_paths = [cwd] + list(cwd.parents)[:3]  # cwd + up to 3 parents

    for base in search_paths:
        base = Path(base)
        for name in DEFAULT_ENV_FILES:
            path = base / name
            if path.exists():
                return path

    return None

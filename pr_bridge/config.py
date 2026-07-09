"""
config.py - Cross-platform storage for provider credentials.

Credentials are stored as JSON in the user's OS-appropriate config directory,
so pr-bridge behaves identically on Windows, macOS, and Linux without relying
on shell-specific environment variables (which are awkward to set persistently
and differ between PowerShell, cmd, and POSIX shells).

Environment variables still take precedence when set, so CI pipelines can inject
credentials without writing a file.

    Bitbucket:  BITBUCKET_ACCESS_TOKEN / BITBUCKET_BEARER_TOKEN
                BITBUCKET_EMAIL (or BITBUCKET_USERNAME) + BITBUCKET_API_TOKEN
    GitHub:     GH_TOKEN / GITHUB_TOKEN

Stored file layout (credentials.json):

    {
      "bitbucket": {"email": "...", "api_token": "...", "access_token": "..."},
      "github": {"token": "..."}
    }
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

APP_NAME = "pr-bridge"

# Provider keys (kept as plain literals so this module has no import cycle
# with fetcher.py, which imports from here).
GITHUB = "github"
BITBUCKET = "bitbucket"


def config_dir() -> Path:
    """Return the OS-appropriate config directory for pr-bridge."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    # macOS and Linux: honor XDG, otherwise ~/.config
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / APP_NAME


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def load_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(data: dict) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    # Tighten to owner-only where the platform supports it (no-op on Windows).
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def provider_credentials(provider: str) -> dict:
    value = load_credentials().get(provider)
    return value if isinstance(value, dict) else {}


def update_provider(provider: str, values: dict) -> Path:
    """Merge non-empty ``values`` into the stored block for ``provider``."""
    data = load_credentials()
    existing = data.get(provider) if isinstance(data.get(provider), dict) else {}
    merged = {**existing, **{k: v for k, v in values.items() if v}}
    data[provider] = merged
    return save_credentials(data)


def get_bitbucket_auth() -> dict:
    """Merge env vars (priority) with stored config for Bitbucket."""
    stored = provider_credentials(BITBUCKET)
    return {
        "email": (
            os.getenv("BITBUCKET_EMAIL")
            or os.getenv("BITBUCKET_USERNAME")
            or stored.get("email")
            or stored.get("username")
        ),
        "api_token": os.getenv("BITBUCKET_API_TOKEN") or stored.get("api_token"),
        "access_token": (
            os.getenv("BITBUCKET_ACCESS_TOKEN")
            or os.getenv("BITBUCKET_BEARER_TOKEN")
            or stored.get("access_token")
        ),
    }


def get_github_token() -> str | None:
    """Return a GitHub token from env (priority) or stored config."""
    return (
        os.getenv("GH_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or provider_credentials(GITHUB).get("token")
    )

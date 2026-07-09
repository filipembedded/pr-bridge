"""
auth.py - Interactive credential setup for pr-bridge.

`pr-bridge auth <provider>` prompts for the required credentials and stores
them in a cross-platform config file (see config.py), so users never have to
export environment variables. Secrets are read with getpass so they are not
echoed to the terminal.
"""

from __future__ import annotations

import getpass
import sys

from . import config

BITBUCKET_TOKEN_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"
GITHUB_TOKEN_URL = "https://github.com/settings/tokens"


def _prompt(label: str, *, secret: bool = False, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        if secret:
            value = getpass.getpass(f"{label}{suffix}: ")
        else:
            value = input(f"{label}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    return value.strip() or default


def auth_bitbucket() -> None:
    print("Configure Bitbucket Cloud credentials for pr-bridge.\n")
    print(f"Create an API token with scopes at:\n  {BITBUCKET_TOKEN_URL}")
    print("App: Bitbucket. Select BOTH of these Read scopes:")
    print("  - read:pullrequest:bitbucket")
    print("  - read:repository:bitbucket   (required for the PR diff)\n")

    existing = config.provider_credentials(config.BITBUCKET)
    email = _prompt("Atlassian account email", default=existing.get("email", ""))
    api_token = _prompt("API token", secret=True)

    if not email or not api_token:
        print("Both email and API token are required.", file=sys.stderr)
        sys.exit(1)

    path = config.update_provider(
        config.BITBUCKET,
        {"email": email, "api_token": api_token},
    )
    print(f"\nSaved Bitbucket credentials to:\n  {path}")


def auth_github() -> None:
    print("Configure GitHub credentials for pr-bridge.\n")
    print("pr-bridge fetches GitHub PRs via the GitHub CLI (gh). You can either:")
    print("  1. Run 'gh auth login' (recommended), or")
    print("  2. Paste a Personal Access Token here (needs 'repo' read access).")
    print(f"Create a token at:\n  {GITHUB_TOKEN_URL}\n")

    token = _prompt("GitHub token (leave blank to skip)", secret=True)
    if not token:
        print("No token entered. pr-bridge will rely on 'gh auth login'.")
        return

    path = config.update_provider(config.GITHUB, {"token": token})
    print(f"\nSaved GitHub token to:\n  {path}")


def cmd_auth(provider: str) -> None:
    if provider == config.BITBUCKET:
        auth_bitbucket()
    elif provider == config.GITHUB:
        auth_github()
    else:  # pragma: no cover - argparse restricts choices
        print(f"Unknown provider: {provider}", file=sys.stderr)
        sys.exit(1)

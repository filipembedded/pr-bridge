"""
fetcher.py - Fetches PR data from GitHub using the gh CLI.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class PRInfo:
    owner: str
    repo: str
    number: int
    title: str
    author: str
    url: str
    base_branch: str
    head_branch: str
    state: str
    body: str


def _run_gh(args: list[str]) -> dict | list:
    """
    Run a gh CLI command and return parsed JSON output.
    When --paginate is used, gh emits one JSON array per page concatenated
    together, so we wrap the output in a list and flatten it.
    """
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        raw = result.stdout.strip()
        if not raw:
            return []

        # --paginate emits multiple JSON arrays back-to-back: [][]. Wrap them
        # in a top-level array and let the decoder pick them up via raw_decode.
        paginated = args and "--paginate" in args
        if paginated:
            decoder = json.JSONDecoder()
            combined: list = []
            idx = 0
            while idx < len(raw):
                # Skip whitespace between pages
                while idx < len(raw) and raw[idx] in " \t\n\r":
                    idx += 1
                if idx >= len(raw):
                    break
                page, end_idx = decoder.raw_decode(raw, idx)
                if isinstance(page, list):
                    combined.extend(page)
                else:
                    combined.append(page)
                idx = end_idx
            return combined

        return json.loads(raw)

    except subprocess.CalledProcessError as e:
        print(f"Error running gh CLI: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(
            "Error: 'gh' CLI not found. Install it from https://cli.github.com/",
            file=sys.stderr,
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing gh CLI output: {e}", file=sys.stderr)
        sys.exit(1)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse GitHub PR URL and return (owner, repo, pr_number)."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    # Expected: github.com/owner/repo/pull/NUMBER
    if len(parts) < 4 or parts[2] != "pull":
        print(
            f"Error: Invalid PR URL format: {url}\n"
            "Expected: https://github.com/owner/repo/pull/NUMBER",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        owner = parts[0]
        repo = parts[1]
        pr_number = int(parts[3])
    except (ValueError, IndexError):
        print(f"Error: Could not parse PR number from URL: {url}", file=sys.stderr)
        sys.exit(1)
    return owner, repo, pr_number


def fetch_pr_info(owner: str, repo: str, pr_number: int) -> PRInfo:
    """Fetch basic PR metadata."""
    data = _run_gh([
        "api",
        f"repos/{owner}/{repo}/pulls/{pr_number}",
    ])
    return PRInfo(
        owner=owner,
        repo=repo,
        number=pr_number,
        title=data.get("title", ""),
        author=data.get("user", {}).get("login", "unknown"),
        url=data.get("html_url", ""),
        base_branch=data.get("base", {}).get("ref", ""),
        head_branch=data.get("head", {}).get("ref", ""),
        state=data.get("state", ""),
        body=data.get("body") or "",
    )


def fetch_review_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """
    Fetch inline review comments (pull request review comments).
    These are comments attached to specific lines in the diff.
    """
    return _run_gh([
        "api",
        "--paginate",
        f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
    ])


def fetch_issue_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """
    Fetch general PR comments (issue-level comments, i.e. the comment box
    below the PR description, not inline diff comments).
    """
    return _run_gh([
        "api",
        "--paginate",
        f"repos/{owner}/{repo}/issues/{pr_number}/comments",
    ])


def fetch_reviews(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch review summaries (APPROVED, CHANGES_REQUESTED, COMMENTED, etc.)."""
    return _run_gh([
        "api",
        f"repos/{owner}/{repo}/pulls/{pr_number}/reviews",
    ])

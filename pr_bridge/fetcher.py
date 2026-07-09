"""
fetcher.py - Fetches pull request data from supported providers.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from .config import get_bitbucket_auth, get_github_token


GITHUB = "github"
BITBUCKET = "bitbucket"
BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0"


@dataclass(frozen=True)
class PRReference:
    provider: str
    owner: str
    repo: str
    number: int


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
    provider: str = GITHUB
    provider_label: str = "GitHub"


@dataclass
class DiffHunk:
    old_path: str
    new_path: str
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class BitbucketAPIError(Exception):
    pass


_BITBUCKET_PR_CACHE: dict[tuple[str, str, int], dict] = {}
_BITBUCKET_COMMENTS_CACHE: dict[tuple[str, str, int], list[dict]] = {}
_BITBUCKET_DIFF_CACHE: dict[tuple[str, str, int], str] = {}


def provider_label(provider: str) -> str:
    if provider == GITHUB:
        return "GitHub"
    if provider == BITBUCKET:
        return "Bitbucket"
    return provider


def _run_gh(args: list[str]) -> dict | list:
    """
    Run a gh CLI command and return parsed JSON output.
    When --paginate is used, gh emits one JSON array per page concatenated
    together, so we wrap the output in a list and flatten it.
    """
    cmd = ["gh"] + args

    # Let gh pick up a token stored via `pr-bridge auth github` when the
    # environment does not already provide one. gh honors GH_TOKEN.
    env = os.environ.copy()
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        token = get_github_token()
        if token:
            env["GH_TOKEN"] = token

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            env=env,
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


def _bitbucket_path(*parts: str | int) -> str:
    return "/".join(quote(str(part), safe="{}") for part in parts)


def _bitbucket_auth_header() -> str | None:
    creds = get_bitbucket_auth()

    bearer_token = creds.get("access_token")
    if bearer_token:
        return f"Bearer {bearer_token}"

    username = creds.get("email")
    api_token = creds.get("api_token")
    if username and api_token:
        raw = f"{username}:{api_token}".encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        return f"Basic {encoded}"

    return None


def _format_bitbucket_error(error: HTTPError, url: str) -> str:
    detail = ""
    try:
        body = error.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        detail = parsed.get("error", {}).get("message") or parsed.get("message") or body
    except Exception:
        detail = error.reason or ""

    message = f"Error calling Bitbucket API ({error.code}) for {url}"
    if detail:
        message += f": {detail}"
    if error.code in (401, 403):
        message += (
            "\nThe Bitbucket API token needs BOTH scopes:"
            "\n  - read:pullrequest:bitbucket"
            "\n  - read:repository:bitbucket   (the PR diff endpoint redirects to"
            " the repository diff, which requires repository read)."
            "\nRun 'pr-bridge auth bitbucket' to (re)enter your credentials, or set"
            " BITBUCKET_EMAIL + BITBUCKET_API_TOKEN (or BITBUCKET_ACCESS_TOKEN)."
        )
    return message


def _bitbucket_request(url: str, accept: str = "application/json") -> str:
    headers = {
        "Accept": accept,
        "User-Agent": "pr-bridge",
    }
    auth_header = _bitbucket_auth_header()
    if auth_header:
        headers["Authorization"] = auth_header

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise BitbucketAPIError(_format_bitbucket_error(e, url)) from e
    except URLError as e:
        raise BitbucketAPIError(f"Error calling Bitbucket API for {url}: {e.reason}") from e


def _run_bitbucket_json(path_or_url: str, paginated: bool = False) -> dict | list:
    url = path_or_url if path_or_url.startswith("http") else f"{BITBUCKET_API_BASE}/{path_or_url}"

    try:
        if not paginated:
            raw = _bitbucket_request(url)
            return json.loads(raw) if raw.strip() else {}

        values: list[dict] = []
        while url:
            raw = _bitbucket_request(url)
            page = json.loads(raw) if raw.strip() else {}
            if not isinstance(page, dict) or "values" not in page:
                return page
            values.extend(page.get("values") or [])
            url = page.get("next")
        return values

    except BitbucketAPIError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing Bitbucket API output: {e}", file=sys.stderr)
        sys.exit(1)


def _try_run_bitbucket_text(path: str) -> str:
    url = f"{BITBUCKET_API_BASE}/{path}"
    try:
        return _bitbucket_request(url, accept="text/plain")
    except BitbucketAPIError as e:
        print(f"Warning: {e}", file=sys.stderr)
        return ""


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL and return (owner, repo, pr_number)."""
    ref = _parse_github_pr_url(url)
    return ref.owner, ref.repo, ref.number


def parse_pull_request_url(url: str) -> PRReference:
    """Parse a supported pull request URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host == "github.com":
        return _parse_github_pr_url(url)
    if host == "bitbucket.org":
        return _parse_bitbucket_pr_url(url)
    if host == "api.bitbucket.org":
        return _parse_bitbucket_api_pr_url(url)

    print(
        f"Error: Unsupported PR provider in URL: {url}\n"
        "Expected a GitHub URL like https://github.com/owner/repo/pull/123 "
        "or a Bitbucket Cloud URL like "
        "https://bitbucket.org/workspace/repo_slug/pull-requests/123",
        file=sys.stderr,
    )
    sys.exit(1)


def _parse_github_pr_url(url: str) -> PRReference:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    # Expected: github.com/owner/repo/pull/NUMBER
    if parsed.netloc.lower() != "github.com" or len(parts) < 4 or parts[2] != "pull":
        print(
            f"Error: Invalid GitHub PR URL format: {url}\n"
            "Expected: https://github.com/owner/repo/pull/NUMBER",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return PRReference(
            provider=GITHUB,
            owner=parts[0],
            repo=parts[1],
            number=int(parts[3]),
        )
    except (ValueError, IndexError):
        print(f"Error: Could not parse PR number from URL: {url}", file=sys.stderr)
        sys.exit(1)


def _parse_bitbucket_pr_url(url: str) -> PRReference:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    # Expected: bitbucket.org/workspace/repo_slug/pull-requests/NUMBER
    if len(parts) < 4 or parts[2] not in {"pull-requests", "pullrequests"}:
        print(
            f"Error: Invalid Bitbucket PR URL format: {url}\n"
            "Expected: https://bitbucket.org/workspace/repo_slug/pull-requests/NUMBER",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return PRReference(
            provider=BITBUCKET,
            owner=parts[0],
            repo=parts[1],
            number=int(parts[3]),
        )
    except (ValueError, IndexError):
        print(f"Error: Could not parse PR number from URL: {url}", file=sys.stderr)
        sys.exit(1)


def _parse_bitbucket_api_pr_url(url: str) -> PRReference:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    # Expected: api.bitbucket.org/2.0/repositories/workspace/repo_slug/pullrequests/NUMBER
    if (
        len(parts) < 6
        or parts[0] != "2.0"
        or parts[1] != "repositories"
        or parts[4] != "pullrequests"
    ):
        print(
            f"Error: Invalid Bitbucket API PR URL format: {url}\n"
            "Expected: https://api.bitbucket.org/2.0/repositories/"
            "workspace/repo_slug/pullrequests/NUMBER",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return PRReference(
            provider=BITBUCKET,
            owner=parts[2],
            repo=parts[3],
            number=int(parts[5]),
        )
    except (ValueError, IndexError):
        print(f"Error: Could not parse PR number from URL: {url}", file=sys.stderr)
        sys.exit(1)


def fetch_pr_info_for_ref(ref: PRReference) -> PRInfo:
    if ref.provider == GITHUB:
        return fetch_pr_info(ref.owner, ref.repo, ref.number)
    if ref.provider == BITBUCKET:
        return fetch_bitbucket_pr_info(ref.owner, ref.repo, ref.number)
    _unsupported_provider(ref.provider)


def fetch_review_comments_for_ref(ref: PRReference) -> list[dict]:
    if ref.provider == GITHUB:
        return fetch_review_comments(ref.owner, ref.repo, ref.number)
    if ref.provider == BITBUCKET:
        return fetch_bitbucket_review_comments(ref.owner, ref.repo, ref.number)
    _unsupported_provider(ref.provider)


def fetch_issue_comments_for_ref(ref: PRReference) -> list[dict]:
    if ref.provider == GITHUB:
        return fetch_issue_comments(ref.owner, ref.repo, ref.number)
    if ref.provider == BITBUCKET:
        return fetch_bitbucket_issue_comments(ref.owner, ref.repo, ref.number)
    _unsupported_provider(ref.provider)


def fetch_reviews_for_ref(ref: PRReference) -> list[dict]:
    if ref.provider == GITHUB:
        return fetch_reviews(ref.owner, ref.repo, ref.number)
    if ref.provider == BITBUCKET:
        return fetch_bitbucket_reviews(ref.owner, ref.repo, ref.number)
    _unsupported_provider(ref.provider)


def _unsupported_provider(provider: str):
    print(f"Error: Unsupported provider: {provider}", file=sys.stderr)
    sys.exit(1)


def fetch_pr_info(owner: str, repo: str, pr_number: int) -> PRInfo:
    """Fetch basic GitHub PR metadata."""
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
        provider=GITHUB,
        provider_label=provider_label(GITHUB),
    )


def fetch_review_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """
    Fetch GitHub inline review comments.
    These are comments attached to specific lines in the diff.
    """
    return _run_gh([
        "api",
        "--paginate",
        f"repos/{owner}/{repo}/pulls/{pr_number}/comments",
    ])


def fetch_issue_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """
    Fetch GitHub general PR comments.
    These are issue-level comments, not inline diff comments.
    """
    return _run_gh([
        "api",
        "--paginate",
        f"repos/{owner}/{repo}/issues/{pr_number}/comments",
    ])


def fetch_reviews(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch GitHub review summaries."""
    return _run_gh([
        "api",
        f"repos/{owner}/{repo}/pulls/{pr_number}/reviews",
    ])


def _fetch_bitbucket_pr(owner: str, repo: str, pr_number: int) -> dict:
    cache_key = (owner, repo, pr_number)
    if cache_key not in _BITBUCKET_PR_CACHE:
        path = _bitbucket_path("repositories", owner, repo, "pullrequests", pr_number)
        data = _run_bitbucket_json(path)
        _BITBUCKET_PR_CACHE[cache_key] = data if isinstance(data, dict) else {}
    return _BITBUCKET_PR_CACHE[cache_key]


def _fetch_bitbucket_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    cache_key = (owner, repo, pr_number)
    if cache_key not in _BITBUCKET_COMMENTS_CACHE:
        path = _bitbucket_path("repositories", owner, repo, "pullrequests", pr_number, "comments")
        data = _run_bitbucket_json(f"{path}?pagelen=100", paginated=True)
        _BITBUCKET_COMMENTS_CACHE[cache_key] = data if isinstance(data, list) else []
    return _BITBUCKET_COMMENTS_CACHE[cache_key]


def _fetch_bitbucket_diff(owner: str, repo: str, pr_number: int) -> str:
    cache_key = (owner, repo, pr_number)
    if cache_key not in _BITBUCKET_DIFF_CACHE:
        path = _bitbucket_path("repositories", owner, repo, "pullrequests", pr_number, "diff")
        _BITBUCKET_DIFF_CACHE[cache_key] = _try_run_bitbucket_text(path)
    return _BITBUCKET_DIFF_CACHE[cache_key]


def fetch_bitbucket_pr_info(owner: str, repo: str, pr_number: int) -> PRInfo:
    """Fetch basic Bitbucket Cloud PR metadata."""
    data = _fetch_bitbucket_pr(owner, repo, pr_number)
    summary = data.get("summary") or {}
    rendered = data.get("rendered") or {}
    description = rendered.get("description") or {}

    return PRInfo(
        owner=owner,
        repo=repo,
        number=pr_number,
        title=data.get("title", ""),
        author=_bitbucket_account_name(data.get("author") or {}),
        url=_link_href(data, "html"),
        base_branch=(data.get("destination") or {}).get("branch", {}).get("name", ""),
        head_branch=(data.get("source") or {}).get("branch", {}).get("name", ""),
        state=(data.get("state") or "").lower(),
        body=summary.get("raw") or description.get("raw") or "",
        provider=BITBUCKET,
        provider_label=provider_label(BITBUCKET),
    )


def fetch_bitbucket_review_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch Bitbucket inline comments and replies to inline comments."""
    comments = _fetch_bitbucket_comments(owner, repo, pr_number)
    inline_related_ids = _bitbucket_inline_related_comment_ids(comments)
    diff_text = _fetch_bitbucket_diff(owner, repo, pr_number)

    return [
        _normalize_bitbucket_comment(comment, diff_text)
        for comment in comments
        if comment.get("id") in inline_related_ids
    ]


def fetch_bitbucket_issue_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Fetch Bitbucket general PR comments."""
    comments = _fetch_bitbucket_comments(owner, repo, pr_number)
    inline_related_ids = _bitbucket_inline_related_comment_ids(comments)

    return [
        _normalize_bitbucket_comment(comment, "")
        for comment in comments
        if comment.get("id") not in inline_related_ids
    ]


def fetch_bitbucket_reviews(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Build review summaries from Bitbucket PR participants."""
    data = _fetch_bitbucket_pr(owner, repo, pr_number)
    reviews = []

    for participant in data.get("participants") or []:
        state = _bitbucket_participant_state(participant)
        if not state:
            continue
        reviews.append({
            "user": {"login": _bitbucket_account_name(participant.get("user") or {})},
            "state": state,
            "submitted_at": participant.get("participated_on") or "",
            "body": "",
            "html_url": _link_href(data, "html"),
        })

    return reviews


def _bitbucket_participant_state(participant: dict) -> str:
    state = participant.get("state")
    if state == "approved" or participant.get("approved") is True:
        return "APPROVED"
    if state == "changes_requested":
        return "CHANGES_REQUESTED"
    return ""


def _bitbucket_inline_related_comment_ids(comments: list[dict]) -> set[int]:
    related_ids = {
        comment["id"]
        for comment in comments
        if comment.get("id") is not None and comment.get("inline")
    }

    changed = True
    while changed:
        changed = False
        for comment in comments:
            comment_id = comment.get("id")
            parent_id = (comment.get("parent") or {}).get("id")
            if comment_id is not None and parent_id in related_ids and comment_id not in related_ids:
                related_ids.add(comment_id)
                changed = True

    return related_ids


def _normalize_bitbucket_comment(raw: dict, diff_text: str) -> dict:
    parent = raw.get("parent") or {}
    inline = raw.get("inline") or parent.get("inline") or {}
    content = raw.get("content") or {}
    html_url = _link_href(raw, "html") or _link_href(raw, "code")

    return {
        "id": raw["id"],
        "user": {"login": _bitbucket_account_name(raw.get("user") or {})},
        "body": content.get("raw") or "",
        "path": inline.get("path", ""),
        "line": _bitbucket_inline_line(inline),
        "diff_hunk": _find_bitbucket_diff_hunk(diff_text, inline),
        "created_at": raw.get("created_on", ""),
        "html_url": html_url,
        "in_reply_to_id": parent.get("id"),
        "author_association": "",
        "resolved": raw.get("resolution") is not None if not parent else None,
        "provider_label": provider_label(BITBUCKET),
    }


def _bitbucket_account_name(account: dict) -> str:
    return (
        account.get("nickname")
        or account.get("display_name")
        or account.get("username")
        or account.get("account_id")
        or "unknown"
    )


def _link_href(resource: dict, name: str) -> str:
    return ((resource.get("links") or {}).get(name) or {}).get("href", "")


def _bitbucket_inline_line(inline: dict) -> Optional[int]:
    for key in ("to", "from", "start_to", "start_from"):
        value = inline.get(key)
        if isinstance(value, int):
            return value
    return None


def _find_bitbucket_diff_hunk(diff_text: str, inline: dict) -> str:
    if not diff_text or not inline:
        return ""

    path = inline.get("path") or ""
    line = _bitbucket_inline_line(inline)
    side = "new" if inline.get("to") is not None or inline.get("start_to") is not None else "old"

    file_hunks = []
    for hunk in _parse_unified_diff_hunks(diff_text):
        if path not in {hunk.old_path, hunk.new_path}:
            continue
        file_hunks.append(hunk)
        if line is None:
            continue
        if side == "new" and _line_in_hunk(line, hunk.new_start, hunk.new_end):
            return hunk.text
        if side == "old" and _line_in_hunk(line, hunk.old_start, hunk.old_end):
            return hunk.text

    return file_hunks[0].text if file_hunks else ""


def _line_in_hunk(line: int, start: int, end: int) -> bool:
    if end < start:
        return line == start
    return start <= line <= end


def _parse_unified_diff_hunks(diff_text: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    old_path = ""
    new_path = ""
    current: DiffHunk | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            old_path = ""
            new_path = ""
            current = None
            continue

        if line.startswith("--- "):
            old_path = _normalize_diff_path(line[4:])
            current = None
            continue

        if line.startswith("+++ "):
            new_path = _normalize_diff_path(line[4:])
            current = None
            continue

        if line.startswith("@@ "):
            parsed = _parse_hunk_header(line)
            if parsed is None:
                current = None
                continue
            old_start, old_len, new_start, new_len = parsed
            current = DiffHunk(
                old_path=old_path,
                new_path=new_path,
                old_start=old_start,
                old_end=old_start + old_len - 1,
                new_start=new_start,
                new_end=new_start + new_len - 1,
                lines=[line],
            )
            hunks.append(current)
            continue

        if current is not None:
            current.lines.append(line)

    return hunks


def _normalize_diff_path(raw_path: str) -> str:
    path = raw_path.strip()
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path in {"/dev/null", "dev/null"}:
        return ""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _parse_hunk_header(header: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        return None
    old_start = int(match.group(1))
    old_len = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_len = int(match.group(4) or "1")
    return old_start, old_len, new_start, new_len

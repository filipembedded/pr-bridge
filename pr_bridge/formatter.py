"""
formatter.py - Formats PR data into AI-friendly Markdown.

Design goals:
  - Dense but readable: AI agents benefit from structured, predictable layout.
  - Minimal noise: No avatar URLs, API links, reaction counts, etc.
  - Thread-aware: Replies are nested under their parent comment.
  - Diff context: The relevant diff hunk is shown so the AI knows exactly
    which code the comment refers to.
  - Resolved vs. unresolved: Resolved threads are clearly marked and can be
    filtered out entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .fetcher import PRInfo


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------

@dataclass
class ReviewComment:
    id: int
    author: str
    body: str
    path: str
    line: Optional[int]
    diff_hunk: str
    created_at: str
    html_url: str
    in_reply_to_id: Optional[int]
    is_suggestion: bool
    author_association: str
    resolved: Optional[bool] = None
    provider_label: str = "GitHub"


@dataclass
class CommentThread:
    """A top-level inline comment together with all its replies."""
    root: ReviewComment
    replies: list[ReviewComment] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        """
        GitHub REST comments do not expose thread resolution, so we infer it
        from replies. Bitbucket Cloud normalized comments set root.resolved.
        """
        if self.root.resolved is not None:
            return self.root.resolved
        return len(self.replies) > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_body(body: str) -> str:
    """Strip trailing whitespace from each line."""
    return "\n".join(line.rstrip() for line in body.splitlines()).strip()


def _is_suggestion(body: str) -> bool:
    return "```suggestion" in body


def _extract_diff_hunk_tail(diff_hunk: str, context_lines: int = 6) -> str:
    """
    Return only the last `context_lines` lines of the diff hunk.
    The full hunk can be very long; the tail is what actually triggered
    the comment.
    """
    lines = diff_hunk.splitlines()
    return "\n".join(lines[-context_lines:]) if len(lines) > context_lines else diff_hunk


def _build_threads(raw_comments: list[dict]) -> list[CommentThread]:
    """
    Group flat list of raw review comment dicts into threaded CommentThread
    objects. Replies reference their parent via `in_reply_to_id`.
    """
    roots: list[ReviewComment] = []
    replies: dict[int, list[ReviewComment]] = {}  # parent_id -> [reply, ...]

    for raw in raw_comments:
        comment = ReviewComment(
            id=raw["id"],
            author=raw["user"]["login"],
            body=_clean_body(raw.get("body") or ""),
            path=raw.get("path", ""),
            line=raw.get("original_line") or raw.get("line"),
            diff_hunk=raw.get("diff_hunk", ""),
            created_at=raw.get("created_at", ""),
            html_url=raw.get("html_url", ""),
            in_reply_to_id=raw.get("in_reply_to_id"),
            is_suggestion=_is_suggestion(raw.get("body") or ""),
            author_association=raw.get("author_association", ""),
            resolved=raw.get("resolved") if isinstance(raw.get("resolved"), bool) else None,
            provider_label=raw.get("provider_label", "GitHub"),
        )

        if comment.in_reply_to_id is None:
            roots.append(comment)
        else:
            replies.setdefault(comment.in_reply_to_id, []).append(comment)

    threads = []
    for root in roots:
        thread = CommentThread(root=root, replies=replies.get(root.id, []))
        threads.append(thread)

    # Sort threads by file path then line number for a consistent reading order.
    threads.sort(key=lambda t: (t.root.path, t.root.line or 0))
    return threads


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _render_comment_body(comment: ReviewComment, indent: str = "") -> str:
    lines = []
    association = (
        f" ({comment.author_association.lower()})"
        if comment.author_association
        else ""
    )
    lines.append(f"{indent}**@{comment.author}**{association} - {comment.created_at[:10]}")
    if comment.html_url:
        lines.append(f"{indent}[view on {comment.provider_label}]({comment.html_url})")
    lines.append("")
    for line in comment.body.splitlines():
        lines.append(f"{indent}{line}")
    return "\n".join(lines)


def _render_thread(thread: CommentThread, index: int) -> str:
    parts = []
    root = thread.root
    status = "addressed" if thread.is_resolved else "**OPEN**"

    parts.append(f"### Thread {index} - `{root.path}` (line {root.line}) [{status}]")
    parts.append("")

    hunk_tail = _extract_diff_hunk_tail(root.diff_hunk)
    if hunk_tail:
        parts.append("**Diff context:**")
        parts.append("```diff")
        parts.append(hunk_tail)
        parts.append("```")
    else:
        parts.append("_No diff context available from the provider._")
    parts.append("")

    parts.append(_render_comment_body(root))
    parts.append("")

    if thread.replies:
        parts.append("**Replies:**")
        parts.append("")
        for reply in thread.replies:
            parts.append(_render_comment_body(reply, indent="> "))
            parts.append(">")
            parts.append("")

    return "\n".join(parts)


def format_pr(
    pr_info: PRInfo,
    review_comments: list[dict],
    issue_comments: list[dict],
    reviews: list[dict],
    filter_mode: str = "all",  # "all" | "unresolved"
) -> str:
    """
    Build the full Markdown document.

    Parameters
    ----------
    pr_info        : Basic PR metadata.
    review_comments: Inline diff comments.
    issue_comments : General PR comments.
    reviews        : Review summaries.
    filter_mode    : "all" keeps every thread; "unresolved" drops resolved
                     threads.
    """
    threads = _build_threads(review_comments)

    if filter_mode == "unresolved":
        threads = [t for t in threads if not t.is_resolved]

    lines = []

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines.append(f"# PR #{pr_info.number}: {pr_info.title}")
    lines.append("")
    lines.append(f"- **Repository:** {pr_info.owner}/{pr_info.repo}")
    lines.append(f"- **Author:** @{pr_info.author}")
    lines.append(f"- **State:** {pr_info.state}")
    lines.append(f"- **Branch:** `{pr_info.head_branch}` -> `{pr_info.base_branch}`")
    lines.append(f"- **URL:** {pr_info.url}")
    lines.append(f"- **Filter:** {filter_mode}")
    lines.append("")

    # -----------------------------------------------------------------------
    # Review summaries
    # -----------------------------------------------------------------------
    meaningful_reviews = [
        r for r in reviews
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
        and r.get("user", {}).get("login") != "ghost"
    ]
    if meaningful_reviews:
        lines.append("## Review Summaries")
        lines.append("")
        for r in meaningful_reviews:
            reviewer = r.get("user", {}).get("login", "unknown")
            state = r.get("state", "")
            submitted = (r.get("submitted_at") or "")[:10]
            body = _clean_body(r.get("body") or "")
            lines.append(f"- **@{reviewer}** - `{state}` ({submitted})")
            if body:
                suffix = "..." if len(body) > 200 else ""
                lines.append(f"  > {body[:200]}{suffix}")
        lines.append("")

    # -----------------------------------------------------------------------
    # Inline review threads
    # -----------------------------------------------------------------------
    lines.append("## Inline Review Comments")
    lines.append("")

    if not threads:
        lines.append("_No inline review comments found for the selected filter._")
        lines.append("")
    else:
        lines.append(
            f"_{len(threads)} thread(s) shown "
            f"({'open only' if filter_mode == 'unresolved' else 'all, including addressed'})._"
        )
        lines.append("")

        current_file = None
        thread_index = 1
        for thread in threads:
            if thread.root.path != current_file:
                current_file = thread.root.path
                lines.append("---")
                lines.append(f"## File: `{current_file}`")
                lines.append("")
            lines.append(_render_thread(thread, thread_index))
            thread_index += 1

    # -----------------------------------------------------------------------
    # General (issue-level) PR comments
    # -----------------------------------------------------------------------
    if issue_comments:
        lines.append("---")
        lines.append("## General PR Comments")
        lines.append("")
        for c in issue_comments:
            author = c.get("user", {}).get("login", "unknown")
            association = c.get("author_association", "").lower()
            created = (c.get("created_at") or "")[:10]
            body = _clean_body(c.get("body") or "")
            html_url = c.get("html_url", "")
            association_text = f" ({association})" if association else ""
            view_link = f" - [view on {pr_info.provider_label}]({html_url})" if html_url else ""
            lines.append(f"**@{author}**{association_text} - {created}{view_link}")
            lines.append("")
            lines.append(body)
            lines.append("")

    return "\n".join(lines)

"""
cli.py - Command-line interface for pr-bridge.

Usage:
    pr-bridge fetch <PR_URL> [OPTIONS]

Options:
    --output PATH       Directory or file path for the output Markdown file.
                        Defaults to the current working directory.
                        If a directory is given, the file is named
                        pr-<NUMBER>-<owner>-<repo>.md
    --filter MODE       Which comments to include.
                        all         : Every thread (default).
                        unresolved  : Only threads with no replies yet.
    --no-general        Exclude general (non-inline) PR comments.
    --version           Show version and exit.
    --help              Show this message and exit.
"""

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .fetcher import fetch_pr_info, fetch_review_comments, fetch_issue_comments, fetch_reviews, parse_pr_url
from .formatter import format_pr


def _resolve_output_path(output_arg: str | None, owner: str, repo: str, pr_number: int) -> Path:
    default_name = f"pr-{pr_number}-{owner}-{repo}.md"

    if output_arg is None:
        return Path.cwd() / default_name

    p = Path(output_arg)
    if p.is_dir() or (not p.suffix and not p.exists()):
        # Treat as directory
        p.mkdir(parents=True, exist_ok=True)
        return p / default_name

    # Treat as explicit file path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cmd_fetch(args: argparse.Namespace) -> None:
    pr_url = args.pr_url
    owner, repo, pr_number = parse_pr_url(pr_url)

    print(f"Fetching PR #{pr_number} from {owner}/{repo}...")

    pr_info = fetch_pr_info(owner, repo, pr_number)
    print(f"  ✓ PR info: \"{pr_info.title}\"")

    review_comments = fetch_review_comments(owner, repo, pr_number)
    print(f"  ✓ Inline review comments: {len(review_comments)}")

    issue_comments: list[dict] = []
    if not args.no_general:
        issue_comments = fetch_issue_comments(owner, repo, pr_number)
        print(f"  ✓ General PR comments: {len(issue_comments)}")

    reviews = fetch_reviews(owner, repo, pr_number)
    print(f"  ✓ Review summaries: {len(reviews)}")

    print(f"  Formatting as Markdown (filter={args.filter})...")
    markdown = format_pr(
        pr_info=pr_info,
        review_comments=review_comments,
        issue_comments=issue_comments,
        reviews=reviews,
        filter_mode=args.filter,
    )

    output_path = _resolve_output_path(args.output, owner, repo, pr_number)
    output_path.write_text(markdown, encoding="utf-8")
    print(f"\n✅ Saved to: {output_path}")
    print(f"   Threads shown: {markdown.count('### Thread')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-bridge",
        description=(
            "Export GitHub PR review comments to an AI-friendly Markdown file.\n\n"
            "Requires the GitHub CLI (gh) to be installed and authenticated.\n"
            "See: https://cli.github.com/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pr-bridge {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # -----------------------------------------------------------------------
    # fetch subcommand
    # -----------------------------------------------------------------------
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch PR review comments and export to Markdown.",
        description="Fetch PR review comments from GitHub and save as Markdown.",
    )
    fetch_parser.add_argument(
        "pr_url",
        metavar="PR_URL",
        help="Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/123",
    )
    fetch_parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        default=None,
        help=(
            "Output directory or file path. Defaults to the current directory. "
            "If a directory is given, the file is named pr-<NUMBER>-<owner>-<repo>.md"
        ),
    )
    fetch_parser.add_argument(
        "--filter", "-f",
        metavar="MODE",
        choices=["all", "unresolved"],
        default="all",
        help=(
            "Filter threads to show: "
            "'all' (default) shows every thread; "
            "'unresolved' shows only threads with no replies yet."
        ),
    )
    fetch_parser.add_argument(
        "--no-general",
        action="store_true",
        default=False,
        help="Exclude general (non-inline) PR comments from the output.",
    )
    fetch_parser.set_defaults(func=cmd_fetch)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

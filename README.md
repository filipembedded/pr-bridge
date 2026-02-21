# pr-bridge

> Export GitHub PR review comments to an AI-friendly Markdown file — so your AI coding assistant gets the full context without you having to copy-paste anything.

## The Problem

When working with an AI assistant (like GitHub Copilot) on a pull request, explaining reviewer feedback is tedious:

- You copy-paste comments manually
- The AI has no context about *which line* the comment refers to
- You lose the diff context
- Threading (replies) is invisible

**pr-bridge** solves this by fetching all PR review data via the GitHub CLI and formatting it into a structured Markdown file that any AI agent can read directly.

## Requirements

- [uv](https://docs.astral.sh/uv/) — modern Python package manager
- [GitHub CLI (`gh`)](https://cli.github.com/) — installed and authenticated (`gh auth login`)

## Installation

```bash
# Install directly from GitHub
uv tool install git+https://github.com/filipembedded/pr-bridge.git

# Or install in editable mode from a local clone
git clone https://github.com/filipembedded/pr-bridge.git
cd pr-bridge
uv tool install -e .
```

Install `uv` if you don't have it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Usage

```bash
# Fetch all review comments for a PR
pr-bridge fetch https://github.com/owner/repo/pull/123

# Fetch only unresolved (unanswered) threads
pr-bridge fetch https://github.com/owner/repo/pull/123 --filter unresolved

# Save output to a specific directory
pr-bridge fetch https://github.com/owner/repo/pull/123 --output ./reviews/

# Save to a specific file
pr-bridge fetch https://github.com/owner/repo/pull/123 --output my-review.md

# Exclude general (non-inline) comments
pr-bridge fetch https://github.com/owner/repo/pull/123 --no-general
```

The output file is saved as `pr-<NUMBER>-<owner>-<repo>.md` in the current directory (or the path you specify).

## Output Format

The generated Markdown is structured for AI consumption:

```
# PR #123: Fix something important

- Repository: owner/repo
- Author: @someone
- State: open
- Branch: `fix/something` → `main`

## Review Summaries
- @reviewer — `CHANGES_REQUESTED` (2024-01-15)

## Inline Review Comments

---
## File: `src/main.c`

### Thread 1 — `src/main.c` (line 42) [**OPEN**]

**Diff context:**
\`\`\`diff
+int foo() {
+    return bar;
+}
\`\`\`

**@reviewer** (member) · 2024-01-15
[view on GitHub](https://github.com/...)

This function is never called. Consider removing it.
```

## Options

| Option | Description |
|--------|-------------|
| `--filter all` | Show all threads (default) |
| `--filter unresolved` | Show only threads with no replies |
| `--output PATH` | Output directory or file (default: current directory) |
| `--no-general` | Exclude general PR comments |
| `--version` | Show version |

## How It Works

1. Parses the GitHub PR URL to extract owner, repo, and PR number
2. Uses `gh api` to fetch inline comments, general comments, and review summaries
3. Groups inline comments into threads (root comment + replies)
4. Renders everything as structured Markdown
5. Saves the file locally for your AI assistant to read

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.

## License

MIT

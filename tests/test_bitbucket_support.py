import unittest

from pr_bridge.fetcher import (
    BITBUCKET,
    PRInfo,
    _bitbucket_inline_related_comment_ids,
    _normalize_bitbucket_comment,
    parse_pull_request_url,
)
from pr_bridge.formatter import format_pr


class ParsePullRequestUrlTests(unittest.TestCase):
    def test_parses_github_url(self):
        ref = parse_pull_request_url("https://github.com/owner/repo/pull/123")

        self.assertEqual(ref.provider, "github")
        self.assertEqual(ref.owner, "owner")
        self.assertEqual(ref.repo, "repo")
        self.assertEqual(ref.number, 123)

    def test_parses_bitbucket_url(self):
        ref = parse_pull_request_url(
            "https://bitbucket.org/workspace/repo_slug/pull-requests/456/overview"
        )

        self.assertEqual(ref.provider, BITBUCKET)
        self.assertEqual(ref.owner, "workspace")
        self.assertEqual(ref.repo, "repo_slug")
        self.assertEqual(ref.number, 456)

    def test_parses_bitbucket_api_url(self):
        ref = parse_pull_request_url(
            "https://api.bitbucket.org/2.0/repositories/workspace/repo/pullrequests/7"
        )

        self.assertEqual(ref.provider, BITBUCKET)
        self.assertEqual(ref.owner, "workspace")
        self.assertEqual(ref.repo, "repo")
        self.assertEqual(ref.number, 7)


class BitbucketCommentTests(unittest.TestCase):
    def test_inline_related_ids_include_nested_replies(self):
        comments = [
            {"id": 1, "inline": {"path": "src/main.c", "to": 10}},
            {"id": 2, "parent": {"id": 1}},
            {"id": 3, "parent": {"id": 2}},
            {"id": 4},
        ]

        self.assertEqual(_bitbucket_inline_related_comment_ids(comments), {1, 2, 3})

    def test_normalizes_comment_and_extracts_diff_hunk(self):
        diff_text = "\n".join([
            "diff --git a/src/main.c b/src/main.c",
            "--- a/src/main.c",
            "+++ b/src/main.c",
            "@@ -1,3 +1,4 @@",
            " int main(void) {",
            "+    return helper();",
            " }",
        ])
        raw = {
            "id": 10,
            "user": {"nickname": "reviewer"},
            "content": {"raw": "Please revisit this."},
            "inline": {"path": "src/main.c", "to": 2},
            "links": {"html": {"href": "https://bitbucket.org/ws/repo/pull-requests/1#comment-10"}},
            "created_on": "2026-07-01T10:00:00+00:00",
            "resolution": None,
        }

        normalized = _normalize_bitbucket_comment(raw, diff_text)

        self.assertEqual(normalized["user"]["login"], "reviewer")
        self.assertEqual(normalized["path"], "src/main.c")
        self.assertEqual(normalized["line"], 2)
        self.assertEqual(normalized["provider_label"], "Bitbucket")
        self.assertFalse(normalized["resolved"])
        self.assertIn("@@ -1,3 +1,4 @@", normalized["diff_hunk"])
        self.assertIn("+    return helper();", normalized["diff_hunk"])


class FormatterResolutionTests(unittest.TestCase):
    def test_bitbucket_resolution_overrides_reply_inference(self):
        pr_info = PRInfo(
            owner="workspace",
            repo="repo",
            number=1,
            title="Example",
            author="author",
            url="https://bitbucket.org/workspace/repo/pull-requests/1",
            base_branch="main",
            head_branch="feature",
            state="open",
            body="",
            provider=BITBUCKET,
            provider_label="Bitbucket",
        )
        comments = [
            {
                "id": 1,
                "user": {"login": "reviewer"},
                "body": "Please fix this.",
                "path": "src/main.c",
                "line": 2,
                "diff_hunk": "@@ -1,2 +1,2 @@\n-return 1;\n+return 2;",
                "created_at": "2026-07-01T10:00:00+00:00",
                "html_url": "https://bitbucket.org/workspace/repo/pull-requests/1#comment-1",
                "in_reply_to_id": None,
                "author_association": "",
                "resolved": False,
                "provider_label": "Bitbucket",
            },
            {
                "id": 2,
                "user": {"login": "author"},
                "body": "I replied, but did not resolve it yet.",
                "path": "",
                "line": None,
                "diff_hunk": "",
                "created_at": "2026-07-01T10:05:00+00:00",
                "html_url": "https://bitbucket.org/workspace/repo/pull-requests/1#comment-2",
                "in_reply_to_id": 1,
                "author_association": "",
                "resolved": None,
                "provider_label": "Bitbucket",
            },
        ]

        markdown = format_pr(
            pr_info=pr_info,
            review_comments=comments,
            issue_comments=[],
            reviews=[],
            filter_mode="unresolved",
        )

        self.assertIn("### Thread 1", markdown)
        self.assertIn("[**OPEN**]", markdown)
        self.assertIn("view on Bitbucket", markdown)


if __name__ == "__main__":
    unittest.main()

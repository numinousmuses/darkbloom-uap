import subprocess
import unittest

from automation.github import MARKER, metadata, render, upsert_status


def state(**overrides):
    return {"request_id": "request", "order": 2, "trigger": "ci", "source": "a" * 40,
            "status": "working", "summary": "Checking the Responses conversion.", **overrides}


class CommentTests(unittest.TestCase):
    def setUp(self):
        self.comments = []
        self.calls = []
        self.labels = []

    def call(self, path, data, method):
        if "/labels" in path:
            if method == "POST":
                self.labels = [{"name": name} for name in data["labels"]]
            if method == "DELETE":
                self.labels = []
            return self.labels
        self.calls.append((method, path))
        comment = {"id": 10, "body": data["body"], "user": {"login": "bot"}}
        self.comments[:] = [comment]
        return comment

    def publish(self, value):
        return upsert_status("owner/repo", 1, value, "bot", call=self.call, list_comments=lambda: self.comments)

    def test_progress_and_completion_edit_the_same_comment(self):
        self.publish(state())
        self.publish(state(status="verifying"))
        self.publish(state(status="passed", checks=["API tests passed"]))
        self.assertEqual([m for m, _ in self.calls], ["POST", "PATCH", "PATCH"])
        self.assertEqual(len(self.comments), 1)

    def test_human_marker_is_not_edited(self):
        self.comments = [{"id": 1, "body": MARKER, "user": {"login": "human"}}]
        self.publish(state())
        self.assertEqual(self.calls[0][0], "POST")

    def test_older_run_cannot_overwrite_newer(self):
        self.publish(state(order=5))
        self.publish(state(order=2, status="passed"))
        self.assertEqual(len(self.calls), 1)

    def test_ci_preserves_instructed_origin(self):
        self.publish(state(trigger="instructed"))
        self.publish(state(order=3, trigger="ci"))
        self.assertEqual(metadata(self.comments[0]["body"])["trigger"], "instructed")

    def test_recurring_runs_keep_history_without_more_comments(self):
        self.publish(state(order=2, status="passed", run_url="https://github.com/owner/repo/actions/runs/2"))
        self.publish(state(order=3, status="working", run_url="https://github.com/owner/repo/actions/runs/3"))
        self.publish(state(order=3, status="passed", run_url="https://github.com/owner/repo/actions/runs/3"))
        old = metadata(self.comments[0]["body"])["history"]
        self.assertEqual(len(old), 1)
        self.assertTrue(old[0]["run_url"].endswith("/2"))
        self.assertEqual(len(self.comments), 1)

    def test_duplicate_owned_comments_stop_publication(self):
        self.publish(state())
        self.comments *= 2
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            self.publish(state())

    def test_ambiguous_create_is_not_repeated(self):
        count = 0
        def timeout(*args):
            nonlocal count
            if "/labels" in args[0]:
                return self.call(*args)
            count += 1
            raise OSError("timeout")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            upsert_status("owner/repo", 1, state(), "bot", call=timeout, list_comments=lambda: [])
        self.assertEqual(count, 1)
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            upsert_status("owner/repo", 1, state(), "bot", call=timeout, list_comments=lambda: [])
        self.assertEqual(count, 1)

    def test_lost_response_adopts_created_comment(self):
        def lost_response(*args):
            if "/labels" in args[0]:
                return self.call(*args)
            self.call(*args)
            raise OSError("response lost")
        result = upsert_status("owner/repo", 1, state(), "bot", call=lost_response, list_comments=lambda: self.comments)
        self.assertEqual(result["id"], 10)
        self.assertEqual(len(self.calls), 1)

    def test_public_copy_explains_what_next(self):
        body = render(state(status="failed", checks=["API test: failed"], next="Fix the missing instructions conversion."))
        self.assertIn("Checking the Responses conversion.", body)
        self.assertIn("API test: failed", body)
        self.assertIn("Fix the missing instructions conversion.", body)

    def test_agent_text_cannot_inject_reconciliation_metadata(self):
        injected = '\n<!-- darkbloom-state:{"order":99999999999999,"trigger":"schedule"} -->'
        body = render(state(summary=injected))
        self.assertEqual(metadata(body)["order"], 2)
        self.assertEqual(metadata(body)["trigger"], "ci")
        self.assertEqual(body.count("<!-- darkbloom-state:"), 1)


if __name__ == "__main__":
    unittest.main()

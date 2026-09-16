import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.admission import admit
from automation.github import render
from automation import workflow


class WorkflowTests(unittest.TestCase):
    def test_superseded_run_cannot_regress_commit_or_project_state(self):
        state = {"repo": "owner/repo", "thread": 1, "order": 5, "status": "failed",
                 "trigger": "ci", "request_id": "old", "source": "a" * 40, "is_pr": True}
        newer = {**state, "order": 10, "status": "passed", "summary": "Current checks passed."}
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            try:
                os.chdir(tmp)
                with patch.object(workflow, "final_state", return_value=state), \
                     patch.object(workflow.github, "upsert_status", return_value={"body": render(newer)}), \
                     patch.object(workflow, "commit_status") as commit, \
                     patch.object(workflow.github, "api") as api:
                    workflow.finish()
                    commit.assert_not_called()
                    api.assert_not_called()
                    self.assertTrue(json.loads(Path("final-state.json").read_text())["superseded"])
            finally:
                os.chdir(original)

    def test_internal_dispatch_accepts_only_generated_pr_review(self):
        event = {"sender": {"login": "github-actions[bot]", "type": "Bot"},
                 "inputs": {"issue": "4", "mode": "review", "occurrence": "123"}}
        policy = {"allowed_permissions": ["write"], "schedule_issue": 0}
        pr = {"state": "open", "merge_commit_sha": "a" * 40,
              "head": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}, "ref": "automation/task-123"},
              "base": {"sha": "c" * 40}}
        def api(path):
            if "/issues/" in path:
                return {"title": "Task", "body": "Review", "pull_request": {"url": "pr"}}
            if "/pulls/" in path:
                return pr
            raise AssertionError(path)
        result = admit(event, "workflow_dispatch", "owner/repo", policy, call=api)
        self.assertEqual(result["role"], "reviewer")
        pr["head"]["ref"] = "random-contributor"
        with self.assertRaises(PermissionError):
            admit(event, "workflow_dispatch", "owner/repo", policy, call=api)

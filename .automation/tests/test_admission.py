import unittest

from automation.admission import admit, current

POLICY = {"allowed_permissions": ["admin", "maintain", "write"], "schedule_issue": 4}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.permission = "write"
        self.pr = {"state": "open", "merge_commit_sha": "a" * 40,
                   "head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}}

    def call(self, path):
        if path.endswith("/permission"):
            return {"permission": self.permission}
        if "/pulls/" in path:
            return self.pr
        if "/issues/" in path:
            return {"title": "Fix parsing", "body": "Keep empty values.", "pull_request": {"url": "example"}}
        raise AssertionError(path)

    def event(self, body="/darkbloom fix the parser"):
        return {"repository": {"full_name": "owner/repo"}, "action": "created",
                "sender": {"login": "person", "type": "User"}, "issue": {"number": 4},
                "comment": {"id": 23, "body": body}}

    def test_ordinary_discussion_does_not_launch_agents(self):
        self.assertIsNone(admit(self.event("Could /darkbloom fix this?"), "issue_comment", "owner/repo", POLICY, call=self.call))

    def test_current_permission_not_author_association_controls_spend(self):
        self.permission = "read"
        with self.assertRaises(PermissionError):
            admit(self.event(), "issue_comment", "owner/repo", POLICY, call=self.call)

    def test_bot_cannot_trigger_itself(self):
        event = self.event()
        event["sender"]["type"] = "Bot"
        self.assertIsNone(admit(event, "issue_comment", "owner/repo", POLICY, call=self.call))

    def test_requests_are_stable_but_changed_instruction_is_new(self):
        first = admit(self.event(), "issue_comment", "owner/repo", POLICY, call=self.call)
        second = admit(self.event(), "issue_comment", "owner/repo", POLICY, call=self.call)
        third = admit(self.event("/darkbloom fix something else"), "issue_comment", "owner/repo", POLICY, call=self.call)
        self.assertEqual(first["request_id"], second["request_id"])
        self.assertNotEqual(first["request_id"], third["request_id"])
        self.assertEqual(first["source"], "a" * 40)

    def test_external_pr_is_accepted_for_ci(self):
        event = {"action": "opened", "number": 4}
        result = admit(event, "pull_request_target", "owner/repo", POLICY, call=self.call)
        self.assertEqual(result["trigger"], "ci")
        self.assertEqual(result["role"], "reviewer")

    def test_changed_head_or_base_invalidates_result(self):
        request = admit(self.event(), "issue_comment", "owner/repo", POLICY, call=self.call)
        self.assertTrue(current(request, call=self.call))
        self.pr["base"]["sha"] = "d" * 40
        self.assertFalse(current(request, call=self.call))

    def test_repository_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mismatch"):
            admit(self.event(), "issue_comment", "other/repo", POLICY, call=self.call)

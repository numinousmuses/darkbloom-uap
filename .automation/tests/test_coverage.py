import unittest
from automation.verification import require_coverage


class CoverageTests(unittest.TestCase):
    def test_protocol_recipe_cannot_approve_api_change(self):
        with self.assertRaisesRegex(ValueError, "does not cover"):
            require_coverage(["coordinator/api/responses.go"], "protocol")

    def test_go_recipe_cannot_approve_rust_sidecar(self):
        with self.assertRaisesRegex(ValueError, "does not cover"):
            require_coverage(["coordinator/promptsidecar/src/main.rs"], "coordinator")

    def test_response_tests_cover_handlers_with_docs(self):
        require_coverage(["coordinator/api/responses.go", "docs/reference/api-contracts.md"], "responses")

    def test_candidate_cannot_weaken_workflow(self):
        with self.assertRaisesRegex(ValueError, "does not cover"):
            require_coverage([".github/workflows/ci.yml"], "coordinator")

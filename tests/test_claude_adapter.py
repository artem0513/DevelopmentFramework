"""Claude protocol coverage only; no installed/authenticated backend required."""
from test_runner import TransportFixture
from ase.adapters.claude import ClaudeAdapter


class ClaudeAdapterTests(TransportFixture):
    def test_claude_missing_structured_output_is_blocked(self):
        result = self.run_backend(ClaudeAdapter(), "claude", "missing")
        self.assertEqual(result["result"]["status"], "BLOCKED")
        self.assertEqual(result["result"]["blocker"], "STRUCTURED_OUTPUT_MISSING")

    def test_claude_permission_denials_are_preserved_and_block(self):
        result = self.run_backend(ClaudeAdapter(), "claude", "denied")
        self.assertEqual(result["result"]["status"], "BLOCKED")
        self.assertEqual(result["result"]["permission_denials"][0]["tool_name"], "Bash")

    def test_claude_schema_output_and_safe_mode_protocol(self):
        result = self.run_backend(ClaudeAdapter(), "claude")
        self.assertEqual(result["result"]["status"], "COMPLETE", result)
        self.assertEqual(result["receipt"]["permission_description"]["os_sandbox"], "not-established")

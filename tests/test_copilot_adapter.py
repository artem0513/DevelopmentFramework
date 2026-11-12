"""Conservative Copilot envelope fixtures, not a claim of verified live format."""
from test_runner import TransportFixture
from ase.adapters.copilot import CopilotAdapter


class CopilotAdapterTests(TransportFixture):
    def test_copilot_requires_terminal_result_and_valid_schema(self):
        for case in ("truncated", "unknown", "malformed", "schema"):
            with self.subTest(case=case):
                result = self.run_backend(CopilotAdapter(), "copilot", case)
                self.assertEqual(result["result"]["status"], "BLOCKED", result)

    def test_copilot_final_envelope_is_only_protocol_evidence(self):
        result = self.run_backend(CopilotAdapter(), "copilot")
        self.assertEqual(result["result"]["status"], "COMPLETE", result)
        self.assertEqual(result["receipt"]["evidence_kind"], "protocol_only")

    def test_copilot_live_execution_blocks_without_clean_start_evidence(self):
        result = self.run_backend(CopilotAdapter(), 'copilot', protocol_only=False)
        self.assertEqual(result['result']['status'], 'BLOCKED')
        self.assertEqual(result['result']['blocker'], 'COPILOT_CLEAN_START_UNVERIFIED')
        self.assertIsNone(result['receipt']['pid'])

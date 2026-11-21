"""Claude protocol coverage only; no installed/authenticated backend required."""
import json
from pathlib import Path

from test_runner import TransportFixture
from ase.adapters import invocation
from ase.adapters.claude import ClaudeAdapter
from ase.protocol import packet_prompt
from ase.runner import Runner, verify_receipt


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

    def test_prompt_travels_on_stdin_after_a_bare_print_flag(self):
        caps = {"argv_prefix": ["claude"], "trusted_cwd": "x", "available": True}
        request = self.request()
        call = ClaudeAdapter().build_invocation(request, caps)
        self.assertEqual(call["argv"][-1], "-p")
        self.assertEqual(call["stdin"], packet_prompt(request))
        self.assertNotIn(packet_prompt(request), call["argv"])

    def test_prompt_beyond_the_windows_command_line_limit_is_delivered_whole(self):
        request = self.request()
        body = "x = 1\n" * 12000  # ~72 KB: rejected before as PROMPT_ARGUMENT_TOO_LARGE on Windows
        import hashlib
        request["source_packet"]["files"][0].update(content=body, sha256=hashlib.sha256(body.encode()).hexdigest())
        outcome = Runner(self.config("claude"), self.base / "artifacts").run(ClaudeAdapter(), request, timeout=10)
        self.assertEqual(outcome["result"]["status"], "COMPLETE", outcome)
        captured = json.loads(Path(outcome["receipt"]["artifacts"]["request"]["path"]).read_bytes())
        stdin = Path(outcome["receipt"]["artifacts"]["stdin"]["path"]).read_bytes()
        self.assertGreater(len(stdin), 28000)
        self.assertEqual(stdin, packet_prompt(captured).encode("utf-8"))
        self.assertEqual(outcome["receipt"]["argv"][-1], "-p")
        self.assertTrue(verify_receipt(outcome["receipt"])["valid"])

    def as_legacy_argument_capture(self, outcome):
        """Rewrite a real capture into the pre-stdin shape: prompt as the -p value, empty stdin."""
        import hashlib
        receipt = json.loads(json.dumps(outcome["receipt"]))
        stdin_path = Path(receipt["artifacts"]["stdin"]["path"])
        prompt = stdin_path.read_bytes().decode("utf-8")
        stdin_path.write_bytes(b"")
        receipt["artifacts"]["stdin"].update(bytes=0, sha256=hashlib.sha256(b"").hexdigest())
        receipt["stdin_sha256"] = hashlib.sha256(b"").hexdigest()
        receipt["argv"] = [*receipt["argv"], prompt]
        stored = stdin_path.parent / "receipt.json"
        stored.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt

    def test_historical_argument_delivery_receipts_still_verify(self):
        legacy = self.as_legacy_argument_capture(self.run_backend(ClaudeAdapter(), "claude"))
        self.assertEqual(legacy["argv"][-2], "-p")
        self.assertTrue(verify_receipt(legacy)["valid"])

    def test_stdin_capture_without_a_bare_print_flag_is_rejected(self):
        outcome = self.run_backend(ClaudeAdapter(), "claude")
        receipt = json.loads(json.dumps(outcome["receipt"]))
        receipt["argv"] = receipt["argv"][:-1]
        stored = Path(receipt["artifacts"]["request"]["path"]).parent / "receipt.json"
        saved = json.loads(stored.read_bytes())
        saved["argv"] = receipt["argv"]
        stored.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from the delivered prompt"):
            verify_receipt(receipt)

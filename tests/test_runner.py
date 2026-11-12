"""Protocol-only subprocess fixtures; these are not independent model judgments."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ase.runner import Runner, verify_receipt
from ase.adapters.codex import CodexAdapter
from ase.protocol import validate_payload


SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["PASS", "FAIL"]}},
          "required": ["verdict"], "additionalProperties": False}

FIXTURE = r'''
import json, os, pathlib, subprocess, sys, time, uuid
case, backend, *args = sys.argv[1:]
flags = {
 "codex": "--ignore-user-config --ephemeral --sandbox --skip-git-repo-check --cd --output-schema --output-last-message --json",
 "claude": "--safe-mode --tools --strict-mcp-config --mcp-config --no-session-persistence --output-format --json-schema",
 "copilot": "--output-format --no-custom-instructions --disable-builtin-mcps --no-auto-update --no-ask-user --no-experimental --no-bash-env --deny-tool"
}
if args == ['--version']:
 print(backend + ' protocol-fixture-1'); sys.exit()
if args == (['exec','--help'] if backend == 'codex' else ['--help']):
 print(flags[backend]); sys.exit()
input_text = sys.stdin.read()
assert bool(input_text) == (backend == 'codex'), 'inherited or missing input'
canary = pathlib.Path('.backend-startup.py')
if canary.exists(): exec(canary.read_text())
if case == 'sleep':
 child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
 pathlib.Path('descendant.pid').write_text(str(child.pid))
 time.sleep(30)
if case == 'flood':
 while True: print('x' * 4096, flush=True)
payload = {'verdict':'PASS'}
if case == 'schema': payload = {'verdict':True}
if case == 'numeric-mismatch': payload = {'count':1}
if case == 'coverage': payload = {'verdict':'PASS','missing_coverage':['service integration not observed']}
if case == 'plain': print('PASS'); sys.exit()
sid = str(uuid.uuid4())
if backend == 'codex':
 expected = ['exec','--ignore-user-config','--ephemeral','--sandbox','read-only','--skip-git-repo-check','--cd',os.getcwd(),'--output-schema',args[9],'--output-last-message',args[11],'--json','-']
 assert args == expected, repr(args)
 assert json.loads(pathlib.Path(args[9]).read_text())['type'] == 'object'
 pathlib.Path(args[11]).write_text(json.dumps(payload))
 if case == 'overfinal': pathlib.Path(args[11]).write_bytes(b'x' * 65536)
 print(json.dumps({'type':'thread.started','thread_id':sid}))
 print(json.dumps({'type':'turn.started'}))
 if case == 'tool': print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'echo unsafe','exit_code':0}}))
 message = {'verdict':'FAIL'} if case == 'mismatch' else {'count':True} if case == 'numeric-mismatch' else payload
 print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(message)}}))
 if case != 'truncated': print(json.dumps({'type':'turn.completed','usage':{}}))
elif backend == 'claude':
 expected = ['--safe-mode','--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--no-session-persistence','--output-format','json','--json-schema',args[10],'-p',args[-1]]
 assert args == expected, repr(args)
 result = {'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':'PASS','permission_denials':[]}
 if case != 'missing': result['structured_output'] = payload
 if case == 'denied': result['permission_denials'] = [{'tool_name':'Bash','tool_input':{'command':'write'}}]
 print(json.dumps(result))
else:
 expected = ['--output-format','json','--no-custom-instructions','--disable-builtin-mcps','--no-auto-update','--no-ask-user','--no-experimental','--no-bash-env','--deny-tool','shell,write,read,url,memory','-p',args[-1]]
 assert args == expected, repr(args)
 print(json.dumps({'type':'assistant.message','data':{'content':json.dumps(payload)}}))
 if case != 'truncated': print(json.dumps({'type':'unknown.result' if case == 'unknown' else 'result','subtype':'success','is_error':False,'session_id':sid,'result':json.dumps(payload)}))
 if case == 'malformed': print('{bad json')
if case == 'exit': sys.exit(9)
'''


class TransportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ase-transport-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.trusted = self.base / "trusted"
        self.trusted.mkdir()
        self.target = self.base / "target"
        self.target.mkdir()
        self.fixture = self.base / "fixture.py"
        self.fixture.write_text(FIXTURE, encoding="utf-8")

    def config(self, backend="codex", case="success", **extra):
        return {"executable": sys.executable, "executable_args": [str(self.fixture), case, backend],
                "trusted_cwd": str(self.trusted), "protocol_only": True, **extra}

    def request(self, role="ac"):
        return {"role": role, "project": {"root": str(self.target)}, "schema": SCHEMA,
                "bindings": {"fingerprint": "a" * 64, "contract_sha256": "b" * 64,
                             "proposal_sha256": "c" * 64, "plan_sha256": "d" * 64, "check_sha256": "e" * 64},
                "source_packet": {"requirements": ["Return one real verdict"],
                    "files": [{"path": "app.py", "content": "value = 1\n",
                               "sha256": "585c93666fcb046b7b264d3fa73202aa2a38254ae82a4b3ba19e873c2d5a9886"}],
                    "coverage": {"scope": "app.py fixture", "included": ["app.py"], "missing": []}}}

    def run_backend(self, adapter=None, backend="codex", case="success", **config):
        return Runner(self.config(backend, case, **config), self.base / "artifacts").run(
            adapter or CodexAdapter(), self.request(), timeout=3)


class RunnerTests(TransportFixture):
    def test_receipt_requires_real_subprocess(self):
        outcome = self.run_backend()
        self.assertEqual(outcome["result"]["status"], "COMPLETE", outcome)
        receipt = outcome["receipt"]
        self.assertGreater(receipt["pid"], 0)
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["evidence_kind"], "protocol_only")
        self.assertTrue(verify_receipt(receipt, expected_bindings=self.request()["bindings"])["valid"])
        with self.assertRaises(ValueError): verify_receipt({"status":"PASS"})

    def test_two_fresh_roles_and_current_inputs(self):
        runner = Runner(self.config(), self.base / "artifacts")
        runs = [runner.run(CodexAdapter(), self.request(role), timeout=3) for role in ("implementation", "ac", "review")]
        self.assertEqual(len({r["receipt"]["execution_id"] for r in runs}), 3)
        self.assertEqual(len({r["receipt"]["session_id"] for r in runs}), 3)
        wrong = {**self.request()["bindings"], "fingerprint": "f" * 64}
        with self.assertRaises(ValueError): verify_receipt(runs[1]["receipt"], expected_bindings=wrong)

    def test_changed_output_or_request_hash_is_rejected(self):
        receipt = self.run_backend()["receipt"]
        path = Path(receipt["artifacts"]["stdout"]["path"])
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError): verify_receipt(receipt)
        receipt = self.run_backend()["receipt"]
        request_path = Path(receipt["artifacts"]["request"]["path"])
        request_path.write_text('{}', encoding="utf-8")
        with self.assertRaises(ValueError): verify_receipt(receipt)

    def test_plain_pass_nonzero_schema_and_truncated_stream_block(self):
        for case in ("plain", "exit", "schema", "truncated", "mismatch", "tool"):
            with self.subTest(case=case):
                outcome = self.run_backend(case=case)
                self.assertEqual(outcome["result"]["status"], "BLOCKED", outcome)
                with self.assertRaises(ValueError): verify_receipt(outcome["receipt"])

    def test_timeout_and_output_cap_bound_real_children(self):
        for case, config in (("sleep", {}), ("flood", {"max_output_bytes": 8192})):
            with self.subTest(case=case):
                result = self.run_backend(case=case, **config)
                receipt = result["receipt"]
                self.assertEqual(result["result"]["status"], "BLOCKED")
                self.assertTrue(receipt["timed_out"] if case == "sleep" else receipt["output_limited"])
                self.assertLess(receipt["duration_seconds"], 8)
                self.assertLessEqual(Path(receipt["artifacts"]["stdout"]["path"]).stat().st_size, config.get("max_output_bytes", 1048576))
                if case == 'sleep':
                    pid = int((self.trusted / 'descendant.pid').read_text())
                    if os.name == 'nt':
                        import ctypes
                        from ctypes import wintypes
                        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                        kernel.OpenProcess.restype = wintypes.HANDLE
                        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                        handle = kernel.OpenProcess(0x1000, False, pid)
                        if handle:
                            try:
                                code = wintypes.DWORD()
                                self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                                self.assertNotEqual(code.value, 259, 'timed out descendant remains active')
                            finally: kernel.CloseHandle(handle)
                    else:
                        status = Path(f'/proc/{pid}/stat')
                        if status.exists(): self.assertEqual(status.read_text().split()[2], 'Z')

    def test_source_packet_hash_coverage_and_size_are_required(self):
        for change in ("hash", "coverage", "size"):
            request = self.request()
            if change == "hash": request["source_packet"]["files"][0]["sha256"] = "0" * 64
            if change == "coverage": del request["source_packet"]["coverage"]
            if change == "size": request["handoff"] = "x" * 3000000
            with self.subTest(change=change), self.assertRaises(ValueError):
                Runner(self.config(), self.base / "artifacts").run(CodexAdapter(), request, timeout=3)

    def test_large_codex_packet_uses_owned_input_and_preserves_exact_hash(self):
        request = self.request()
        content = 'value = 1\n' * 6000
        request['source_packet']['files'][0].update(content=content, sha256=hashlib.sha256(content.encode()).hexdigest())
        result = Runner(self.config(), self.base / 'artifacts').run(CodexAdapter(), request, timeout=3)
        self.assertEqual(result['result']['status'], 'COMPLETE', result)
        receipt = result['receipt']
        prompt = Path(receipt['artifacts']['stdin']['path']).read_bytes()
        self.assertGreater(len(prompt), 40000)
        self.assertEqual(receipt['stdin_sha256'], hashlib.sha256(prompt).hexdigest())
        self.assertEqual(receipt['argv'][-1], '-')

    def test_schema_rejects_unsupported_constraints_and_bool_integer(self):
        self.assertEqual(validate_payload({"verdict":"PASS"}, SCHEMA)["status"], "VALID")
        self.assertEqual(validate_payload(1, {"type":"integer", "multipleOf":2})["status"], "BLOCKED")
        self.assertEqual(validate_payload(True, {"type":"integer"})["status"], "INVALID")
        self.assertEqual(validate_payload({}, {"type":"object", "properties":{"optional":{"$ref":"somewhere"}}})["status"], "BLOCKED")

    def test_diagnostic_role_uses_a_fresh_bounded_process(self):
        try:
            result = Runner(self.config(), self.base / 'artifacts').run(CodexAdapter(), self.request('diagnostic'), timeout=3)
        except ValueError as error:
            self.fail('Diagnostic role must be supported: ' + str(error))
        self.assertEqual(result['result']['status'], 'COMPLETE')
        self.assertEqual(result['receipt']['role'], 'diagnostic')

    def test_missing_absolute_cli_is_a_typed_blocker_without_role_process(self):
        config = self.config(executable=str(self.base / 'missing.exe'))
        try:
            result = Runner(config, self.base / 'artifacts').run(CodexAdapter(), self.request(), timeout=3)
        except ValueError as error:
            self.fail('Absent CLI must produce a typed unavailable result: ' + str(error))
        self.assertEqual(result['result']['blocker'], 'CLI_NOT_INSTALLED')
        self.assertIsNone(result['receipt']['pid'])

    def test_terminal_metadata_cannot_be_overridden(self):
        receipt = self.run_backend()['receipt']
        receipt['terminal'] = {'type':'turn.completed','fake':True}
        (Path(receipt['artifact_root']) / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaises(ValueError): verify_receipt(receipt)

    def test_structured_final_and_stream_cannot_disagree_on_json_type(self):
        request = self.request()
        request['schema'] = {'type':'object','properties':{'count':{'type':'integer'}},'required':['count'],'additionalProperties':False}
        result = Runner(self.config(case='numeric-mismatch'), self.base / 'artifacts').run(CodexAdapter(), request, timeout=3)
        self.assertEqual(result['result']['status'], 'BLOCKED')
        self.assertEqual(result['result']['blocker'], 'OUTPUT_MISMATCH')

    def test_model_missing_coverage_is_exposed_without_synthesizing_semantic_pass(self):
        request = self.request()
        request['schema'] = {'type':'object','properties':{'verdict':{'type':'string'},'missing_coverage':{'type':'array','items':{'type':'string'}}},'required':['verdict','missing_coverage'],'additionalProperties':False}
        result = Runner(self.config(case='coverage'), self.base / 'artifacts').run(CodexAdapter(), request, timeout=3)
        self.assertEqual(result['result']['missing_coverage'], ['service integration not observed'])
        self.assertEqual(result['result']['payload']['verdict'], 'PASS')
        # COMPLETE is a protocol result. Only the parent semantic gate may approve.
        self.assertEqual(result['result']['status'], 'COMPLETE')

    def test_oversized_final_artifact_is_bounded_even_after_fast_exit(self):
        result = self.run_backend(case='overfinal', max_output_bytes=8192)
        self.assertEqual(result['result']['status'], 'BLOCKED')
        self.assertTrue(result['receipt']['output_limited'])
        raw = Path(result['receipt']['artifact_root']) / 'final.json'
        self.assertLessEqual(raw.stat().st_size, 8192)

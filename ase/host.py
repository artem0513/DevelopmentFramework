"""Host-side MCP verification coordinator. No shell, tests or publishing API.

Start from the Codex MCP host, before entering its tool sandbox. Child model
tools still run in Codex's read-only sandbox. The caller cannot choose a state
directory, executable, environment, command or permission profile.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
import threading
import uuid

from .core import Engine, redact
from . import inspection


class Coordinator:
    def __init__(self, roots, home, codex):
        self.roots = [Path(root).resolve(strict=True) for root in roots]
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise ValueError("At least one existing allowed root is required")
        executable = Path(codex)
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("Codex must be an existing absolute host-configured executable")
        self.codex = str(executable.resolve(strict=True))
        self.home = Path(home).resolve()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._jobs = {}
        self._closed = False

    def start(self, repo, task_id, parallel=True):
        if not isinstance(repo, (str, Path)) or not isinstance(task_id, str):
            raise ValueError("Repository and task identifier must be strings")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task_id) or type(parallel) is not bool:
            raise ValueError("Invalid task identifier or parallel flag")
        requested = Path(repo)
        if not requested.is_absolute():
            raise ValueError("Repository must be an absolute path")
        repository = requested.resolve(strict=True)
        if not any(repository == root or repository.is_relative_to(root) for root in self.roots):
            raise ValueError("Repository outside host-configured allowed roots")
        metadata = repository / ".git"
        if not any(metadata.resolve() == root or metadata.resolve().is_relative_to(root) for root in self.roots):
            raise ValueError("Git metadata escapes host-configured allowed roots")
        if metadata.is_file():
            pointer = metadata.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                raise ValueError("Invalid Git metadata pointer")
            metadata = (repository / pointer[8:]).resolve(strict=True)
        if not metadata.is_dir() or not any(metadata == root or metadata.is_relative_to(root) for root in self.roots):
            raise ValueError("Git directory outside host-configured allowed roots")
        common = metadata / "commondir"
        if common.is_file():
            common_directory = (metadata / common.read_text(encoding="utf-8").strip()).resolve(strict=True)
            if not any(common_directory == root or common_directory.is_relative_to(root) for root in self.roots):
                raise ValueError("Git common directory outside host-configured allowed roots")
        # Engine verifies that this is a Git root. Do not create state for paths
        # outside the allowlist, including junction/symlink escapes.
        with self._lock:
            if self._closed or any(job["status"] == "RUNNING" for job in self._jobs.values()):
                raise ValueError("Verification coordinator busy or closed; poll the active job")
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "status": "RUNNING", "repo": str(repository), "task_id": task_id}
            self._jobs[job_id] = job
            self._pool.submit(self._verify, job_id, repository, task_id, parallel)
            return dict(job)

    def _verify(self, job_id, repository, task_id, parallel):
        engine = None
        try:
            engine = Engine(repository, self.home, codex=self.codex, inspector=inspection,
                            verification_workdir=Path(__file__).resolve().parent)
            verification = engine.verify(task_id, parallel=parallel)
            report = engine.report(task_id)
            outcome = {"status": "COMPLETED", "result": {"verification": verification, "report": report}}
        except Exception as error:
            outcome = {"status": "FAILED", "error": redact(error)}
        finally:
            if engine is not None:
                engine.store.close()
        with self._lock:
            self._jobs[job_id].update(outcome)

    def status(self, job_id):
        with self._lock:
            if not isinstance(job_id, str) or job_id not in self._jobs:
                raise ValueError("Unknown verification job")
            return dict(self._jobs[job_id])

    def close(self):
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)


TOOLS = [
    {"name": "verification_start", "description": "Independently verify an existing Autonomous SE task using separate read-only Codex sessions. Execute check in the chat sandbox first. Returns a job ID; poll verification_status. This does not implement, run test commands, authorize or publish.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"repo": {"type": "string"}, "task_id": {"type": "string"}, "parallel": {"type": "boolean", "default": True}},
                     "required": ["repo", "task_id"]}},
    {"name": "verification_status", "description": "Read an independent verification job. COMPLETED means processes finished; inspect result.report.quality_gate.status for APPROVED, REJECTED or BLOCKED.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}}
]


def dispatch(coordinator, message):
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "autonomous-se-verification", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method != "tools/call":
        raise ValueError("Unsupported method")
    name, arguments = params.get("name"), params.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be an object")
    if name == "verification_start":
        if set(arguments) - {"repo", "task_id", "parallel"} or not {"repo", "task_id"}.issubset(arguments):
            raise ValueError("Only repo, task_id and parallel are accepted")
        result = coordinator.start(**arguments)
    elif name == "verification_status":
        if set(arguments) != {"job_id"}:
            raise ValueError("Only job_id is accepted")
        result = coordinator.status(**arguments)
    else:
        raise ValueError("Unknown tool; only verification is available")
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--codex", required=True)
    args = parser.parse_args()
    coordinator = Coordinator(args.root, args.home, args.codex)
    try:
        for line in sys.stdin:
            message = None
            try:
                if len(line) > 65536:
                    raise ValueError("Request exceeds 64 KiB")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Request must be an object")
                if "id" not in message:
                    continue
                response = {"jsonrpc": "2.0", "id": message["id"], "result": dispatch(coordinator, message)}
            except Exception as error:
                response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                            "error": {"code": -32602, "message": redact(error)}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        coordinator.close()


if __name__ == "__main__":
    main()

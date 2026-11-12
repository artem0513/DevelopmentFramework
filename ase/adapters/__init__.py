"""Explicit CLI backends. Native-agent callbacks are advisory, not receipts."""
import hashlib
from pathlib import Path
from typing import Protocol

from ..protocol import normalized


class BackendAdapter(Protocol):
    name: str
    def probe(self, config: dict) -> dict: ...
    def build_invocation(self, request: dict, capabilities: dict) -> dict: ...
    def parse_result(self, raw_events: list[dict], final_payload: object) -> dict: ...


def probe_cli(name, config, required_flags):
    # Imports lazily to avoid a runner/adapter import cycle.
    from ..runner import capture_process, checked_path
    cwd = checked_path(config["trusted_cwd"], directory=True)
    executable = Path(config["executable"])
    if not executable.is_absolute(): raise ValueError("Absolute host-configured executable required")
    if not executable.exists():
        # Validate existing ancestors without requiring the missing executable.
        ancestor = executable.parent
        while not ancestor.exists(): ancestor = ancestor.parent
        checked_path(ancestor, directory=True)
        return {"backend": name, "installed": False, "available": False, "blocker": "CLI_NOT_INSTALLED",
                "version": None, "executable": str(executable), "executable_sha256": None,
                "argv_prefix": [str(executable)], "trusted_cwd": str(cwd),
                "auth_availability": "not-probed", "protocol_only": config.get("protocol_only") is True,
                "structured_output": False, "clean_start": False}
    executable = checked_path(executable, directory=False)
    prefix = config.get("executable_args", [])
    if not isinstance(prefix, list) or not all(isinstance(x, str) and "\0" not in x for x in prefix):
        raise ValueError("Invalid host-configured executable prefix")
    argv = [str(executable), *prefix]
    version = capture_process([*argv, "--version"], cwd, timeout=10, max_output_bytes=65536)
    help_args = ["exec", "--help"] if name == "codex" else ["--help"]
    help_result = capture_process([*argv, *help_args], cwd, timeout=10, max_output_bytes=65536)
    help_text = help_result["stdout"].decode("utf-8", "replace")
    supported = all(flag in help_text for flag in required_flags)
    available = version["exit_code"] == 0 and help_result["exit_code"] == 0 and supported
    return {"backend": name, "installed": version["exit_code"] == 0,
            "version": version["stdout"].decode("utf-8", "replace").strip(),
            "executable": str(executable), "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "argv_prefix": argv, "trusted_cwd": str(cwd), "available": available,
            "blocker": None if available else "CLEAN_START_UNAVAILABLE",
            "help_sha256": hashlib.sha256(help_result["stdout"]).hexdigest(),
            "auth_availability": "not-probed", "protocol_only": config.get("protocol_only") is True,
            "structured_output": True, "clean_start": supported,
            "permission_policy": "packet-only; no target tools authorized", "os_sandbox": "not-established"}


def invocation(capabilities, args, *, os_sandbox="not-established"):
    if not capabilities.get("available"):
        raise ValueError(capabilities.get("blocker") or "BACKEND_UNAVAILABLE")
    return {"argv": [*capabilities["argv_prefix"], *args], "cwd": capabilities["trusted_cwd"], "stdin": None,
            "permission_description": {"policy": "packet-only; no target tools authorized", "os_sandbox": os_sandbox},
            "artifact_paths": capabilities.get("artifact_paths", {})}


def get_adapter(name):
    from .codex import CodexAdapter
    from .claude import ClaudeAdapter
    from .copilot import CopilotAdapter
    backends = {"codex": CodexAdapter, "claude": ClaudeAdapter, "copilot": CopilotAdapter}
    if name not in backends: raise ValueError("Unknown explicitly selected backend")
    return backends[name]()

"""Supervised packet implementation, actual checks and bounded repair cycles."""
from datetime import datetime
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import threading
import tempfile
import uuid
import weakref

from .adapters import get_adapter
from .gates import GateService, source_resources, source_snapshot, stage_checks
from .profiles import validate_access, validate_evidence_spec, has_software
from .runner import Runner, verify_receipt
from .state import append_event, encode, hash_value, read_record, text, transaction, write_record
from .store import now
from .projects import _safe_state
from .tasks import TaskService


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STR = {"type": "string"}
STRINGS = {"type": "array", "items": STR}
AC_SCHEMA = _object({"criteria": {"type": "array", "items": _object({
    "id": STR, "status": {"type": "string", "enum": ["PASS", "FAIL", "UNVERIFIED", "BLOCKED", "NOT_APPLICABLE"]},
    "expected": STR, "evidence": STRINGS, "method": STR, "limitations": STRINGS, "missing_coverage": STRINGS})}})
REVIEW_SCHEMA = _object({"findings": {"type": "array", "items": _object({
    "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]}, "path": STR,
    "line": {"type": "integer", "minimum": 1}, "message": STR})}, "summary": STR})
EDIT_SCHEMA = _object({"edits": {"type": "array", "maxItems": 100, "items": _object({
    "path": STR, "operation": {"type": "string", "enum": ["write", "delete"]},
    "before_sha256": STR, "content": STR})}, "summary": STR})
DIAGNOSTIC_SCHEMA = _object({"cause": STR, "recommendation": STR, "requires_new_approval": {"type": "boolean"}})


@contextmanager
def _exclusive(path):
    """OS releases this advisory lock even when the coordinator crashes."""
    _safe_state(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        raise ValueError("Invalid execution lock")
    with path.open("a+b") as stream:
        try:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another execution owns this root task") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _target(root, value):
    from .project_local import is_private_path
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("Path escapes approved scope")
    candidate = root / value
    if is_private_path(value) or (candidate.resolve().is_relative_to(root) and is_private_path(candidate.resolve().relative_to(root))):
        raise ValueError("Private project-local state is outside source edit/packet scope")
    if (candidate.is_symlink() or not candidate.resolve().is_relative_to(root)
            or any(part.lower() == ".git" for part in candidate.resolve().relative_to(root).parts)):
        raise ValueError("Path escapes approved scope")
    if candidate.is_file() and candidate.stat().st_nlink > 1:
        raise ValueError("Hardlinked source cannot be mutated")
    return candidate


def apply_edits(root, scope, edits):
    root = Path(root).resolve()
    if "file_write" not in scope["effects"] and edits:
        raise ValueError("File mutation exceeds approved scope effects")
    checked, seen = [], set()
    for edit in edits:
        path = _target(root, edit.get("path"))
        allowed = False
        for resource in scope["resources"]:
            boundary = _target(root, resource)
            if path == boundary or (boundary.is_dir() and path.is_relative_to(boundary)):
                allowed = True
        if not allowed or path in seen:
            raise ValueError("Edit exceeds approved scope or duplicates a path")
        seen.add(path)
        before = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if (edit.get("before_sha256") or None) != before:
            raise ValueError("Edit preimage is stale")
        if edit.get("operation", "write") not in {"write", "delete"} or not isinstance(edit.get("content"), str):
            raise ValueError("Invalid edit operation")
        if path.exists() and not path.is_file():
            raise ValueError("Edit target is not a file")
        checked.append((path, edit))
    for path, edit in checked:
        if edit.get("operation", "write") == "delete":
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".ase-" + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(edit["content"], encoding="utf-8", newline="")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return [str(path.relative_to(root)).replace("\\", "/") for path, _ in checked]


class WorkflowController:
    def __init__(self, store, runner=None):
        self.store = store
        self.tasks = TaskService(store)
        self.runner = runner
        self._ownership_state = threading.local()

    @contextmanager
    def _ownership(self, task_id):
        root_id = self._root_task(task_id)
        held = getattr(self._ownership_state, "held", set())
        if root_id in held:
            yield
            return
        with _exclusive(self.store.root / "tasks" / root_id / "execution.lock"):
            self._ownership_state.held = held | {root_id}
            try:
                yield
            finally:
                self._ownership_state.held = held

    def _root_task(self, task_id):
        seen = set()
        while True:
            if task_id in seen:
                raise ValueError("Parent task cycle")
            seen.add(task_id)
            task = self.tasks.snapshot(task_id)
            parent = task["contract"].get("parent_task_id")
            if not parent:
                return task_id
            task_id = parent

    def reserve_cycle(self, task_id, stage_id):
        self.tasks.require_approval(task_id, stage_id)
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            if read_record(db, "stop", task_id)["payload"] or read_record(db, "stop", root_id)["payload"]:
                raise ValueError("Task is paused by a stop request")
            root = self.tasks.snapshot(root_id)
            policy = root["contract"]["execution_policy"]
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            if policy["active_time_budget_seconds"] is not None and value.get("active_seconds", 0) >= policy["active_time_budget_seconds"]:
                raise ValueError("Active time budget exhausted")
            key = task_id + ":" + stage_id
            count = value["stages"].get(key, 0)
            if count >= policy["max_stage_cycles"] or value["task_cycles"] >= policy["max_task_cycles"]:
                raise ValueError("Implementation cycle budget exhausted")
            value["task_cycles"] += 1
            value["stages"][key] = count + 1
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        return {"task_cycles": value["task_cycles"], "stage_cycles": count + 1}

    def charge_time(self, task_id, seconds):
        if seconds < 0:
            raise ValueError("Elapsed time cannot be negative")
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            value["active_seconds"] = value.get("active_seconds", 0) + seconds
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])

    def _available(self, task_id, timeout):
        root = self.tasks.snapshot(self._root_task(task_id))
        if read_record(self.store.db, "stop", task_id)["payload"] or read_record(self.store.db, "stop", root["task_id"])["payload"]:
            raise ValueError("Task is paused by a stop request")
        limit = root["contract"]["execution_policy"]["active_time_budget_seconds"]
        used = (read_record(self.store.db, "budget", root["task_id"])["payload"] or {}).get("active_seconds", 0)
        if limit is not None and used >= limit:
            raise ValueError("Active time budget exhausted")
        return min(timeout, limit - used) if limit is not None else timeout

    def _stop_requested(self, task_id):
        return bool(read_record(self.store.db, "stop", task_id)["payload"] or
                    read_record(self.store.db, "stop", self._root_task(task_id))["payload"])

    def _call(self, task_id, function, timeout=1200):
        timeout = self._available(task_id, timeout)
        root_id = self._root_task(task_id)
        nonce = uuid.uuid4().hex
        with transaction(self.store.db) as db:
            old = read_record(db, "active_call", root_id)
            if old["payload"]:
                raise ValueError("An unfinished call needs explicit resume/reconciliation")
            write_record(db, "active_call", root_id, {"started": now(), "timeout": timeout, "owner_nonce": nonce, "pid": os.getpid()}, expected_revision=old["revision"])
        started = time.monotonic()
        try:
            return function(timeout)
        finally:
            with transaction(self.store.db) as db:
                self.charge_time(task_id, time.monotonic() - started)
                old = read_record(db, "active_call", root_id)
                if (old["payload"] or {}).get("owner_nonce") == nonce:
                    write_record(db, "active_call", root_id, None, expected_revision=old["revision"])

    def execute_actions(self, task_id, stage):
        with self._ownership(task_id):
            return self._execute_actions(task_id, stage)

    def _access_stage(self, task_id, spec, stage_id=None, *, allow_terminal=False):
        from .credentials import CredentialError
        access = validate_access(spec["access"])
        task = self.tasks.snapshot(task_id)
        plan = self.tasks.execution_plan(task_id)
        for stage in task["contract"]["stages"]:
            if stage_id is not None and stage["id"] != stage_id:
                continue
            if access["target"] not in stage["scope"]["resources"]:
                continue
            selected = stage_checks(task["contract"], plan["checks"], stage["id"])
            declared = [*selected, *stage.get("actions", [])]
            if not any(item.get("id") == spec.get("id") and item.get("access") == access for item in declared):
                continue
            try:
                self.tasks.require_approval(task_id, stage["id"], allow_terminal=allow_terminal)
            except ValueError:
                if stage_id is not None:
                    raise
                continue
            return stage
        raise CredentialError("access_scope_denied")

    def _access_client(self):
        from .access import CredentialAccess
        from .credentials import CredentialVault
        return CredentialAccess(CredentialVault(self.store.state_home))

    def _execute_command(self, task_id, spec, argv, log, timeout, stage_id=None, *, allow_terminal=False):
        from .core import execute
        cancel = lambda: self._stop_requested(task_id)
        if "access" not in spec:
            return execute(argv, self.store.repo, log, timeout, cancel_requested=cancel)
        self._access_stage(task_id, spec, stage_id, allow_terminal=allow_terminal)
        access = spec["access"]
        receipt = self._access_client().run(access["credential_ref"], access["target"], argv, self.store.repo,
            log, timeout=timeout, cancel_requested=cancel)
        with transaction(self.store.db) as db:
            append_event(db, "access:" + uuid.uuid4().hex, "access_used", task_id, receipt["access"])
        return receipt

    def _declared_access(self, task_id, stage_id, access):
        from .credentials import CredentialError
        validate_access(access)
        task = self.tasks.snapshot(task_id)
        stage = next((s for s in task["contract"]["stages"] if s["id"] == stage_id), None)
        if stage is None:
            raise CredentialError("access_scope_denied")
        declared = [*stage_checks(task["contract"], self.tasks.execution_plan(task_id)["checks"], stage_id), *stage.get("actions", [])]
        spec = next((item for item in declared if item.get("access") == access), None)
        if spec is None:
            raise CredentialError("access_not_planned")
        self._access_stage(task_id, spec, stage_id)
        return spec

    def ensure_access(self, task_id, stage_id, access):
        with self._ownership(task_id):
            self._declared_access(task_id, stage_id, access)
            return self._call(task_id, lambda timeout: self._access_client().ensure(
                access["credential_ref"], access["target"], timeout=timeout,
                cancel_requested=lambda: self._stop_requested(task_id)), 60)

    def request_access(self, task_id, stage_id, access, path="/", method="GET"):
        with self._ownership(task_id):
            self._declared_access(task_id, stage_id, access)
            return self._call(task_id, lambda timeout: self._access_client().request(
                access["credential_ref"], access["target"], path=path, method=method, timeout=timeout,
                cancel_requested=lambda: self._stop_requested(task_id)), 30)

    def _observe_action(self, task_id, stage, action, folder, *, allow_terminal=False):
        proof = self._call(task_id, lambda timeout: self._execute_command(task_id, action,
            action["observation_argv"], folder / (uuid.uuid4().hex + ".log"), timeout, stage["id"],
            allow_terminal=allow_terminal), action.get("timeout", 300))
        if proof.get("exit_code") != 0 or proof.get("error") or proof.get("timed_out") or proof.get("cancelled"):
            raise ValueError("Resource observation failed; effect remains UNKNOWN")
        try:
            value = json.loads(Path(proof["log"]).read_text(encoding="utf-8"))
        except ValueError:
            raise ValueError("Resource observation is not structured JSON; effect remains UNKNOWN") from None
        if (not isinstance(value, dict) or type(value.get("exists")) is not bool
                or (value["exists"] and (not isinstance(value.get("resource_id"), str)
                    or "[REDACTED]" in value["resource_id"]))):
            raise ValueError("Resource observation must prove exists and a nonsecret resource_id")
        observation = {"exists": value["exists"], "resource_id": value.get("resource_id"), "proof_ref": proof["log"] + "#sha256=" + proof["log_sha256"]}
        if "access" in proof:
            observation["access"] = proof["access"]
        return observation

    def _execute_actions(self, task_id, stage):
        """Probe actual state before every effect, including uncertain resumes."""
        from .core import execute
        from .operations import OperationLedger
        self.tasks.require_approval(task_id, stage["id"])
        canonical = next(s for s in self.tasks.snapshot(task_id)["contract"]["stages"] if s["id"] == stage["id"])
        if canonical != stage:
            raise ValueError("Action stage differs from the approved contract")
        ledger = OperationLedger(self.store)
        results = []
        for action in stage.get("actions", []):
            intent = {"project_id": self.store.project_id, "workspace_id": self.store.workspace_id, "action": action}
            action_id = hash_value({"task": task_id, "stage": stage["id"], "action": action["id"]})
            ledger.prepare(task_id, stage["id"], action_id, intent)
            folder = self.store.root / "tasks" / task_id / "actions" / action_id
            _safe_state(folder)
            folder.mkdir(parents=True, exist_ok=True)

            def observe():
                return self._observe_action(task_id, stage, action, folder)

            observation = observe()
            ledger.reconcile(action_id, observation)
            if not observation["exists"]:
                self._available(task_id, action.get("timeout", 300))
                ledger.started(action_id)
                execution = self._call(task_id, lambda timeout: self._execute_command(task_id, action,
                    action["argv"], folder / (uuid.uuid4().hex + ".log"), timeout, stage["id"]), action.get("timeout", 300))
                observation = observe()
                ledger.reconcile(action_id, observation)
                if execution.get("exit_code") != 0 or not observation["exists"]:
                    raise ValueError("Infrastructure action did not establish its actual postcondition")
            results.append(observation)
        return results

    def record_failure(self, task_id, stage_id, reasons):
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            key = task_id + ":" + stage_id + ":" + hash_value(sorted(set(reasons)))
            value["failures"][key] = value["failures"].get(key, 0) + 1
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        limit = self.tasks.snapshot(root_id)["contract"]["execution_policy"]["repeat_failure_limit"]
        return {"diagnostic_required": value["failures"][key] >= limit, "repetitions": value["failures"][key]}

    def reserve_diagnostic(self, task_id, stage_id):
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            key = task_id + ":" + stage_id
            limit = self.tasks.snapshot(root_id)["contract"]["execution_policy"]["max_diagnostic_passes"]
            if value["diagnostics"].get(key, 0) >= limit:
                raise ValueError("diagnostic budget exhausted")
            value["diagnostics"][key] = value["diagnostics"].get(key, 0) + 1
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        return value["diagnostics"][key]

    def request_stop(self, task_id, *, reason):
        from .journal import Journal
        text(reason, "stop reason", limit=4000)
        with transaction(self.store.db) as db:
            task = self.tasks.snapshot(task_id)
            if task["state"] in {"COMPLETED", "CANCELLED"}:
                return {**task, "stop": {"status": "ALREADY_TERMINAL"}}
            record = read_record(db, "stop", task_id)
            if record["payload"] and record["payload"].get("reason") == reason and record["payload"].get("closure"):
                return {**task, "stop": record["payload"]}
            request_id = uuid.uuid4().hex
            root_id = self._root_task(task_id)
            pending = bool(read_record(db, "active_workflow", root_id)["payload"] or read_record(db, "active_call", root_id)["payload"])
            next_action = "Review the saved checkpoint, reconcile any UNKNOWN effects, then explicitly resume task " + task_id
            value = {"reason": reason, "requested_at": now(), "request_id": request_id, "pending_execution_at_request": pending}
            write_record(db, "stop", task_id, value, expected_revision=record["revision"])
            journal = Journal(self.store)
            session_id = "stop-" + request_id
            journal.start_session(task_id, session_id)
            revision = db.execute("SELECT COALESCE(MAX(revision),0) FROM checkpoints WHERE task_id=?", (task_id,)).fetchone()[0]
            closure = journal.finish_session(task_id, session_id, {"finish_reason": "paused",
                "summary": "Pause requested: " + reason, "next_action": next_action,
                "context": {"summary": task["contract"]["request"], "limitations": ["This closure saves the stop request; a running owner records its actual settled outcome separately"]},
                "events": [{"kind": "pause", "summary": reason, "next_action": next_action}],
                "blockers": [{"id": "execution-blocker", "reason": "Paused by request: " + reason, "next_probe": next_action}]},
                closure_key=session_id + ":closure", expected_revision=revision, expected_context_revision=journal.context()["revision"])
            value["closure"] = closure
            latest = read_record(db, "stop", task_id)
            write_record(db, "stop", task_id, value, expected_revision=latest["revision"])
        return {**self.tasks.snapshot(task_id), "stop": value}

    def _save(self, kind, task_id, value):
        with transaction(self.store.db) as db:
            old = read_record(db, kind, task_id)
            write_record(db, kind, task_id, value, expected_revision=old["revision"])
        return value

    def check(self, task_id, stage_id=None):
        with self._ownership(task_id):
            return self._check(task_id, stage_id)

    def _check(self, task_id, stage_id=None):
        from .core import execute
        task = self.tasks.snapshot(task_id)
        approved = task["approved_stage_ids"] or ([task["contract"]["stages"][0]["id"]] if task["contract"].get("parent_task_id") else [])
        if not approved:
            raise ValueError("Checks require current proposal approval")
        self.tasks.require_approval(task_id, stage_id or approved[0], allow_terminal=task["state"] == "COMPLETED")
        if task["state"] == "COMPLETED":
            # Credential rotation permits fresh observations/checks, never replay
            # a completed resource mutation or reopen its business authority.
            from .operations import OperationLedger
            ledger = OperationLedger(self.store)
            for stage in task["contract"]["stages"]:
                if stage_id is not None and stage["id"] != stage_id:
                    continue
                for action in stage.get("actions", []):
                    if "access" not in action:
                        continue
                    action_id = hash_value({"task": task_id, "stage": stage["id"], "action": action["id"]})
                    if read_record(self.store.db, "operation", action_id)["payload"]:
                        folder = self.store.root / "tasks" / task_id / "actions" / action_id
                        _safe_state(folder)
                        folder.mkdir(parents=True, exist_ok=True)
                        ledger.reconcile(action_id, self._observe_action(task_id, stage, action, folder, allow_terminal=True))
        plan = self.tasks.execution_plan(task_id)
        before = source_snapshot(self.store, task["contract"], task_id=task_id)
        folder = self.store.root / "tasks" / task_id / "checks" / uuid.uuid4().hex
        _safe_state(folder)
        folder.mkdir(parents=True)
        results = []
        for raw in stage_checks(task["contract"], plan["checks"], stage_id):
            spec = validate_evidence_spec(raw, task["contract"]["profile"])
            if spec["kind"] == "artifact":
                path = _target(self.store.repo.resolve(), spec["path"])
                result = {"id": spec["id"], "kind": "artifact", "path": str(path),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
            elif spec["kind"] == "manual":
                result = read_record(self.store.db, "manual_evidence", task_id + ":" + spec["id"])["payload"] or {"id": spec["id"], "kind": "manual", "confirmed": False}
            else:
                result = self._call(task_id, lambda timeout: self._execute_command(task_id, spec, spec["argv"],
                    folder / (spec["id"] + ".log"), timeout, stage_id, allow_terminal=task["state"] == "COMPLETED"), spec.get("timeout", 300))
                result.update(id=spec["id"], kind=spec["kind"], parser=spec.get("parser"), configured_argv=spec["argv"])
            results.append(result)
        after = source_snapshot(self.store, task["contract"], task_id=task_id)
        bundle = {"kind": "checks", "task_id": task_id, "fingerprint": before["fingerprint"], "workspace_id": self.store.workspace_id,
                  "contract_hash": task["contract_hash"], "plan_hash": plan["plan_hash"], "results": results,
                  "source_changed_during_checks": before["fingerprint"] != after["fingerprint"], "timestamp": now()}
        return self._save("checks", task_id, bundle)

    def _capture_stage(self, task_id, stage_id, verdict):
        task = self.tasks.snapshot(task_id)
        self._save("stage", task_id + ":" + stage_id, {"state": "COMPLETED", "contract_hash": task["contract_hash"],
            "proposal_hash": task["proposal_hash"], "fingerprint": verdict["fingerprint"], "gate": verdict, "completed_at": now(),
            "proof": {"version": 1, "checks": read_record(self.store.db, "checks", task_id)["payload"],
                      "roles": {role: read_record(self.store.db, "implementation" if role == "implementation" else "verification",
                          task_id if role == "implementation" else task_id + ":" + role)["payload"] for role in ("implementation", "ac", "review")}}})

    def _stage_history(self, task):
        history, missing = {"historical_only": True, "stages": []}, []
        for stage in task["contract"]["stages"]:
            saved = read_record(self.store.db, "stage", task["task_id"] + ":" + stage["id"])["payload"]
            if not saved or saved.get("state") != "COMPLETED":
                continue
            try:
                if saved.get("contract_hash") != task["contract_hash"] or saved.get("proposal_hash") != task["proposal_hash"]:
                    raise ValueError("stale contract/proposal stage history")
                proof = saved["proof"]
                if proof.get("version") != 1 or not saved.get("completed_at"):
                    raise ValueError("stage has no prospective proof capture")
                checks = proof["checks"]
                bindings = {"fingerprint": checks["fingerprint"], "contract_sha256": task["contract_hash"],
                    "proposal_sha256": task["proposal_hash"], "plan_sha256": checks["plan_hash"],
                    "check_sha256": hash_value(checks), "workspace_id": self.store.workspace_id}
                entry = {"stage_id": stage["id"], "completed_at": saved["completed_at"], "contract_hash": saved["contract_hash"],
                    "proposal_hash": saved["proposal_hash"], "gate": saved["gate"], "checks": checks, "roles": {}, "observations": []}
                observations = {}
                for role in ("implementation", "ac", "review"):
                    receipt = proof["roles"][role]
                    root = Path(receipt["artifact_root"])
                    _safe_state(root)
                    if not root.resolve().is_relative_to(self.store.root.resolve()):
                        raise ValueError("historical receipt escapes project state")
                    verified = verify_receipt(receipt, expected_bindings=bindings if role != "implementation" else None)
                    if any(receipt["bindings"].get(key) != bindings[key] for key in ("contract_sha256", "proposal_sha256", "workspace_id", "plan_sha256")):
                        raise ValueError("historical receipt identity differs")
                    request = json.loads(Path(receipt["artifacts"]["request"]["path"]).read_bytes())
                    if request.get("role") != role:
                        raise ValueError("historical role mismatch")
                    handoff = request.get("handoff", {})
                    captured_stage = handoff.get("stage", {}).get("id") if role == "implementation" else handoff.get("stage_id")
                    if captured_stage != stage["id"]:
                        raise ValueError("historical stage boundary mismatch")
                    entry["roles"][role] = {"execution_id": verified["execution_id"], "evidence_kind": verified["evidence_kind"],
                        "bindings": receipt["bindings"], "started_at_utc": receipt.get("started_at_utc"), "ended_at_utc": receipt.get("ended_at_utc"),
                        "session_id": receipt.get("session_id"), "receipt_sha256": hash_value(receipt)}
                    for item in request["source_packet"]["files"]:
                        if item["path"] not in {"_ase_context/source_changes.json", "_ase_context/actual_checks.json"} and not item["path"].startswith("_ase_context/logs/"):
                            continue
                        if len(item["content"].encode("utf-8")) > 65536:
                            raise ValueError("historical observation exceeds packet bound")
                        key = (item["path"], item["sha256"])
                        if key not in observations:
                            observations[key] = {**item, "roles": []}
                        observations[key]["roles"].append(role)
                entry["observations"] = list(observations.values())
                entry["semantic_approval"] = saved["gate"].get("status") == "APPROVED" and all(entry["roles"][role]["evidence_kind"] == "independent_cli" for role in ("ac", "review"))
                if len(encode({"stages": history["stages"] + [entry]}).encode("utf-8")) > 262144:
                    raise ValueError("stage history exceeds bounded packet budget")
                history["stages"].append(entry)
            except (ValueError, OSError, KeyError, TypeError) as error:
                missing.append("Stage history unavailable: " + stage["id"] + ": " + str(error))
        return history, missing

    def _packet(self, task_id):
        task = self.tasks.snapshot(task_id)
        snapshot = source_snapshot(self.store, task["contract"], task_id=task_id)
        baseline = read_record(self.store.db, "baseline", task_id)["payload"]
        changes = None
        if baseline:
            paths = set(baseline["files"]) | set(snapshot["files"])
            changed = sorted(p for p in paths if baseline["files"].get(p) != snapshot["files"].get(p))
            if len(changed) > 1000:
                raise ValueError("Source change inventory exceeds the bounded review packet")
            unchanged = sorted(p for p in paths if baseline["files"].get(p) == snapshot["files"].get(p))
            changes = {"baseline_fingerprint": baseline["fingerprint"], "current_fingerprint": snapshot["fingerprint"],
                       "baseline_dirty": baseline.get("dirty"), "coverage": "Actual content/mode hashes from baseline and current source observations; software covers tracked and nonignored untracked files plus declared dependencies",
                       "changes": [{"path": p, "before_sha256": baseline["files"].get(p), "after_sha256": snapshot["files"].get(p)} for p in changed],
                       "unchanged_paths": unchanged[:1000], "unchanged_path_count": len(unchanged), "unchanged_paths_truncated": len(unchanged) > 1000}
        files, missing = [], []
        selected = set()
        for resource in source_resources(self.store, task["contract"], task_id):
            candidate = self.store.repo / resource
            if candidate.resolve().is_relative_to(self.store.repo.resolve()):
                if candidate.is_dir():
                    selected.update(p for p in snapshot["files"] if (self.store.repo / p).is_relative_to(candidate.resolve()))
                else:
                    selected.add(resource.replace("\\", "/"))
        for path in sorted(selected):
            candidate = self.store.repo / path
            try:
                candidate = _target(self.store.repo.resolve(), path)
                if any(p.lower() == ".git" for p in candidate.resolve().relative_to(self.store.repo.resolve()).parts):
                    raise ValueError("Private Git data is not a source packet")
                if not candidate.exists():
                    continue
                data = candidate.read_bytes()
                if len(data) > 512000:
                    raise ValueError("source exceeds per-file packet bound")
                content = data.decode("utf-8")
                files.append({"path": path, "content": content, "sha256": hashlib.sha256(data).hexdigest()})
            except (UnicodeError, OSError, ValueError) as ex:
                missing.append(path + ": " + str(ex))
        historical = read_record(self.store.db, "baseline_implementation", task_id)["payload"]
        if historical and changes:
            try:
                if not Path(historical["artifact_root"]).resolve().is_relative_to(self.store.root.resolve()):
                    raise ValueError("Historical source capture escapes project state")
                verify_receipt(historical)
                original = json.loads(Path(historical["artifacts"]["request"]["path"]).read_bytes())
                changed_paths = {entry["path"] for entry in changes["changes"]}
                for before in original["source_packet"]["files"]:
                    if before["path"] in changed_paths:
                        files.append({**before, "path": "_ase_before/" + before["path"]})
            except (ValueError, OSError, KeyError, TypeError) as ex:
                missing.append("Historical source capture unavailable: " + str(ex))
        stage_history, history_missing = self._stage_history(task)
        missing.extend(history_missing)
        for name, data in (("contract", task["contract"]), ("execution_plan", self.tasks.execution_plan(task_id)), ("source_changes", changes), ("stage_history", stage_history),
                           ("actual_checks", read_record(self.store.db, "checks", task_id)["payload"])):
            content = encode(data)
            files.append({"path": "_ase_context/" + name + ".json", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        check = read_record(self.store.db, "checks", task_id)["payload"]
        for result in (check or {}).get("results", []):
            if result.get("log"):
                log = Path(result["log"])
                if not log.resolve().is_relative_to(self.store.root.resolve()) or not log.is_file():
                    missing.append("Actual check log unavailable: " + result["id"])
                    continue
                content = log.read_bytes().decode("utf-8", errors="replace")
                files.append({"path": "_ase_context/logs/" + result["id"] + ".txt", "content": content,
                              "sha256": hashlib.sha256(content.encode()).hexdigest()})
        return {"requirements": [task["contract"]["request"], *[ac["id"] + ": " + ac["expected"] for ac in task["contract"]["acceptance"]]],
                "files": files, "coverage": {"scope": "Current declared project source plus task contract and actual check logs; undeclared dependencies are not proven complete",
                                               "included": [f["path"] for f in files], "missing": missing}}

    def _request(self, task_id, role, schema, handoff=None):
        task = self.tasks.snapshot(task_id)
        plan = self.tasks.execution_plan(task_id)
        check = read_record(self.store.db, "checks", task_id)["payload"]
        source = source_snapshot(self.store, task["contract"], task_id=task_id)
        return {"role": role, "project": {"root": str(self.store.repo), "project_id": self.store.project_id}, "schema": schema,
                "bindings": {"fingerprint": source["fingerprint"], "contract_sha256": task["contract_hash"], "proposal_sha256": task["proposal_hash"] or "unapproved",
                             "plan_sha256": plan["plan_hash"], "check_sha256": hash_value(check) if check else "missing", "workspace_id": self.store.workspace_id},
                "source_packet": self._packet(task_id), "handoff": handoff or {}}

    def _runner(self, task_id):
        if self.runner is not None:
            return self.runner
        backend = self.tasks.snapshot(task_id)["contract"]["execution_policy"]["backend"]
        config = read_record(self.store.db, "backend_config", backend)["payload"] or {}
        executable = config.get("executable") or shutil.which({"codex": "codex", "claude": "claude", "copilot": "copilot"}[backend])
        if not executable:
            raise ValueError("CLI_NOT_INSTALLED: " + backend)
        from .project_local import validate_local_state
        temporary = None
        if validate_local_state(self.store.state_home, self.store.repo):
            temporary = tempfile.TemporaryDirectory(prefix="ase-local-runner-")
            trusted = Path(temporary.name)
        else:
            trusted = self.store.state_home / "runner"
            trusted.mkdir(parents=True, exist_ok=True)
        runner = Runner({**config, "executable": str(Path(executable).resolve()), "trusted_cwd": str(trusted)}, self.store.root / "tasks" / task_id / "sessions")
        # Keep the clean empty cwd alive for the Runner's whole operation; only
        # subprocess captures/receipts persist, under the project's private state.
        runner._temporary_cwd = temporary
        if temporary is not None:
            weakref.finalize(runner, temporary.cleanup)
        return runner

    def verify(self, task_id, stage_id=None):
        with self._ownership(task_id):
            return self._verify(task_id, stage_id)

    def _verify(self, task_id, stage_id=None):
        task = self.tasks.snapshot(task_id)
        approved = task["approved_stage_ids"] or ([task["contract"]["stages"][0]["id"]] if task["contract"].get("parent_task_id") else [])
        if not approved:
            raise ValueError("Independent verification requires current proposal approval")
        self.tasks.require_approval(task_id, stage_id or approved[0], allow_terminal=task["state"] == "COMPLETED")
        runner = self._runner(task_id)
        adapter = get_adapter(task["contract"]["execution_policy"]["backend"])
        for role, schema in (("ac", AC_SCHEMA), ("review", REVIEW_SCHEMA)):
            instruction = ("Independently assess the configured criteria against the original request, actual packet source, source_changes baseline manifest, captured before-files and command logs. Return exactly the configured criterion IDs and preserve expected wording. Evaluate original-request constraints under the relevant criteria; a missing material constraint is UNVERIFIED/BLOCKED, never an extra ignored criterion. PASS needs meaningful evidence. Broader limitations do not invalidate concrete evidenced behavior."
                           if role == "ac" else "Independently review actual source for actionable defects P0-P3. P0/P1/P2 block. Do not downgrade to obtain approval. State coverage limits in summary.")
            result = self._call(task_id, lambda timeout: runner.run(adapter, self._request(task_id, role, schema, {"instruction": instruction, "stage_id": stage_id}), timeout=timeout, cancel_requested=lambda: self._stop_requested(task_id)))
            self._save("verification", task_id + ":" + role, result["receipt"])
        return GateService(self.store).gate(task_id, stage_id=stage_id)

    def run(self, task_id, *, resume=False):
        from .journal import Journal
        task = self.tasks.snapshot(task_id)
        if task["legacy_v1"]:
            raise ValueError("Legacy tasks require a reviewed v2 contract")
        if task["state"] == "COMPLETED":
            return GateService(self.store).gate(task_id)
        root_id = self._root_task(task_id)
        with self._ownership(task_id):
            active = read_record(self.store.db, "active_call", root_id)
            if active["payload"]:
                if not resume:
                    raise ValueError("Interrupted call needs explicit resume")
                elapsed = max(0, (datetime.fromisoformat(now()) - datetime.fromisoformat(active["payload"]["started"])).total_seconds())
                with transaction(self.store.db) as db:
                    self.charge_time(task_id, min(elapsed, active["payload"]["timeout"]))
                    write_record(db, "active_call", root_id, None, expected_revision=active["revision"])
            journal = Journal(self.store)
            old_workflow = read_record(self.store.db, "active_workflow", root_id)
            if old_workflow["payload"]:
                previous = old_workflow["payload"]
                old_session = journal.session(previous["task_id"], previous["session_id"])
                if old_session["payload"] and old_session["payload"]["status"] == "RUNNING":
                    previous_task = self.tasks.snapshot(previous["task_id"])
                    revision = self.store.db.execute("SELECT COALESCE(MAX(revision),0) FROM checkpoints WHERE task_id=?", (previous["task_id"],)).fetchone()[0]
                    journal.finish_session(previous["task_id"], previous["session_id"], {"finish_reason": "interrupted",
                        "summary": "Previous coordinator ended without a closure", "next_action": "Reconcile actual effects and resume approved work",
                        "context": {"summary": previous_task["contract"]["request"]}}, closure_key=previous["session_id"] + ":recovered",
                        expected_revision=revision, expected_context_revision=journal.context()["revision"])
            session_id = uuid.uuid4().hex
            journal.start_session(task_id, session_id)
            self._save("active_workflow", root_id, {"task_id": task_id, "session_id": session_id})
            journal.append({"kind": "work_started", "task_id": task_id, "session_id": session_id, "summary": "Resuming approved work" if resume else "Starting approved work"}, event_key=session_id + ":started")
            reason = "blocked"
            try:
                verdict = self._run(task_id, resume=resume)
                reason = "completed" if verdict["status"] == "APPROVED" else "paused" if read_record(self.store.db, "stop", task_id)["payload"] else "awaiting_approval" if verdict.get("awaiting_approval") else "blocked"
            except KeyboardInterrupt:
                reason = "interrupted"
                verdict = {"status": "BLOCKED", "blocked_reasons": ["Execution interrupted; inspect uncertain effects before resume"]}
            except (ValueError, OSError, KeyError, TypeError) as ex:
                from .core import redact
                verdict = {"status": "BLOCKED", "blocked_reasons": [redact(ex)]}
                if read_record(self.store.db, "stop", task_id)["payload"]:
                    reason = "paused"
            with transaction(self.store.db):
                return self._finish_run(task_id, task, session_id, verdict, reason, journal, root_id)

    def _finish_run(self, task_id, task, session_id, verdict, reason, journal, root_id):
        # BEGIN IMMEDIATE serializes this decision with a concurrent stop. A
        # stop committed first wins even when the last verifier returned PASS.
        if self._stop_requested(task_id):
            reason = "paused"
            if verdict["status"] == "APPROVED":
                verdict = {"status": "BLOCKED", "verified_gate": verdict,
                           "blocked_reasons": ["Stop requested before final closure"]}
        remaining = [s["id"] for s in self.tasks.snapshot(task_id)["contract"]["stages"] if (read_record(self.store.db, "stage", task_id + ":" + s["id"])["payload"] or {}).get("state") != "COMPLETED"]
        summary = "All approved task obligations verified" if reason == "completed" else "; ".join(verdict.get("reasons", verdict.get("blocked_reasons", ["Work paused"])))[:3500]
        checkpoint_revision = self.store.db.execute("SELECT COALESCE(MAX(revision),0) FROM checkpoints WHERE task_id=?", (task_id,)).fetchone()[0]
        resolved = []
        if reason == "completed" and any(b["task_id"] == task_id and b["id"] == "execution-blocker" for b in journal._active_blockers(journal._events())):
            resolved = [{"kind": "blocker_resolved", "blocker_id": "execution-blocker", "summary": "Current task verification resolved the framework execution blocker"}]
        closure = journal.finish_session(task_id, session_id, {"finish_reason": reason, "summary": summary,
            "next_action": "No remaining task work" if reason == "completed" else "Inspect reported blockers, reconcile effects, then approve revised scope or resume",
            "context": {"summary": task["contract"]["request"], "limitations": ["This context describes the most recent task; earlier dated snapshots remain available"]},
            "remaining": remaining, "workflow_outcome": verdict["status"], "events": resolved,
            "blockers": [] if reason == "completed" else [{"id": "execution-blocker", "reason": summary, "next_probe": "Inspect current gate and stage state before resume"}]},
            closure_key=session_id + ":finish", expected_revision=checkpoint_revision, expected_context_revision=journal.context()["revision"])
        self._save("active_workflow", root_id, None)
        return {**verdict, "session_id": session_id, "closure": closure}

    def _run(self, task_id, *, resume=False):
        task = self.tasks.snapshot(task_id)
        if task["state"] in {"BLOCKED", "PAUSED"} and not resume:
            return {"status": "BLOCKED", "blocked_reasons": ["Task is stopped or blocked; inspect current evidence and explicitly resume"]}
        if resume:
            if task["state"] == "COMPLETED":
                return GateService(self.store).gate(task_id)
            with transaction(self.store.db) as db:
                stopped = read_record(db, "stop", task_id)
                if stopped["payload"]:
                    write_record(db, "stop", task_id, None, expected_revision=stopped["revision"])
        if not self.tasks.readiness(task_id)["discovery_present"]:
            return {"status": "BLOCKED", "blocked_reasons": ["Scoped environment and capability discovery is required before implementation"]}
        runner = self._runner(task_id)
        adapter = get_adapter(task["contract"]["execution_policy"]["backend"])
        caps = adapter.probe(runner.config)
        if not caps.get("available"):
            return {"status": "BLOCKED", "blocked_reasons": [caps.get("blocker", "INDEPENDENT_SESSION_UNAVAILABLE")]}
        if not read_record(self.store.db, "baseline", task_id)["payload"]:
            baseline = source_snapshot(self.store, task["contract"], task_id=task_id)
            if has_software(task["contract"]):
                from .inspection import safe_git
                baseline["dirty"] = safe_git(self.store.repo, "status", "--porcelain")
            self._save("baseline", task_id, baseline)
        pending = list(task["contract"]["stages"])
        ordered = []
        while pending:
            ready = next(s for s in pending if set(s["depends_on"]).issubset({x["id"] for x in ordered}))
            ordered.append(ready)
            pending.remove(ready)
        for stage in ordered:
            stage_id = stage["id"]
            done = read_record(self.store.db, "stage", task_id + ":" + stage_id)["payload"] or {}
            if done.get("state") == "COMPLETED" and done.get("contract_hash") == task["contract_hash"] and done.get("proposal_hash") == task["proposal_hash"]:
                continue
            try:
                self.tasks.require_approval(task_id, stage_id)
            except ValueError:
                continue
            if any((read_record(self.store.db, "stage", task_id + ":" + dep)["payload"] or {}).get("state") != "COMPLETED" for dep in stage["depends_on"]):
                continue
            child_blockers = GateService(self.store).required_child_blockers(task_id, stage_id)
            if child_blockers:
                return {"status": "BLOCKED", "blocked_reasons": child_blockers}
            while True:
                progress_key = task_id + ":" + stage_id
                progress = read_record(self.store.db, "cycle", progress_key)["payload"] or {}
                if (progress.get("phase") in {"DIAGNOSING", "NEEDS_APPROVAL"}
                        and progress.get("proposal_hash") == task["proposal_hash"]):
                    return {"status": "BLOCKED", "blocked_reasons": ["Diagnostic was interrupted or requires a revised approved proposal"]}
                continuing = (resume and progress.get("phase") in {"CHECKING", "VERIFYING"}
                              and progress.get("contract_hash") == task["contract_hash"]
                              and progress.get("proposal_hash") == task["proposal_hash"])
                if not continuing:
                    try:
                        self.reserve_cycle(task_id, stage_id)
                    except ValueError as ex:
                        return {"status": "BLOCKED", "blocked_reasons": [str(ex)], "task_id": task_id}
                    current = self.tasks.snapshot(task_id)
                    self.tasks.transition(task_id, "EXECUTING", expected_revision=current["revision"], reason="Executing approved stage " + stage_id)
                    progress = {"phase": "IMPLEMENTING", "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"]}
                    self._save("cycle", progress_key, progress)
                    feedback = read_record(self.store.db, "gate", progress_key)["payload"]
                    candidate = self._call(task_id, lambda timeout: runner.run(adapter, self._request(task_id, "implementation", EDIT_SCHEMA,
                        {"instruction": "Propose minimal approved-scope edits. Empty before_sha256 means a new file. Do not change requirements or proof state.",
                         "stage": stage, "previous_feedback": feedback, "diagnostic": read_record(self.store.db, "diagnostic", task_id)["payload"]}), timeout=timeout, cancel_requested=lambda: self._stop_requested(task_id)))
                    self._save("implementation", task_id, candidate["receipt"])
                    if candidate["result"]["status"] != "COMPLETE":
                        return {"status": "BLOCKED", "blocked_reasons": [candidate["result"]["blocker"]]}
                    if not read_record(self.store.db, "baseline_implementation", task_id)["payload"]:
                        self._save("baseline_implementation", task_id, candidate["receipt"])
                    self.tasks.require_approval(task_id, stage_id)
                    self._available(task_id, 1)
                    apply_edits(self.store.repo, stage["scope"], candidate["result"]["payload"]["edits"])
                    progress.update(phase="CHECKING", fingerprint=source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"])
                    self._save("cycle", progress_key, progress)
                if progress["phase"] == "CHECKING":
                    self.execute_actions(task_id, stage)
                current = self.tasks.snapshot(task_id)
                self.tasks.transition(task_id, "VALIDATING", expected_revision=current["revision"], reason="Checking actual stage result")
                # A fresh check also reconciles drift since an interrupted call;
                # it cannot reserve another implementation attempt.
                self.check(task_id, stage_id)
                progress.update(phase="VERIFYING", fingerprint=source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"])
                self._save("cycle", progress_key, progress)
                current = self.tasks.snapshot(task_id)
                self.tasks.transition(task_id, "REVIEWING", expected_revision=current["revision"], reason="Independent acceptance and review")
                verdict = self.verify(task_id, stage_id)
                if verdict["status"] == "APPROVED":
                    self._capture_stage(task_id, stage_id, verdict)
                    from .journal import Journal
                    Journal(self.store).append({"kind": "stage_result", "task_id": task_id, "summary": "Verified stage " + stage_id, "status": "COMPLETED", "evidence_refs": ["stage:" + stage_id]}, event_key=task_id + ":" + stage_id + ":" + task["proposal_hash"])
                    break
                if verdict["blocked_reasons"]:
                    return verdict
                progress.update(phase="REJECTED")
                self._save("cycle", progress_key, progress)
                resume = False
                failure = self.record_failure(task_id, stage_id, verdict["rejected_reasons"])
                if failure["diagnostic_required"]:
                    try:
                        self.reserve_diagnostic(task_id, stage_id)
                    except ValueError as ex:
                        return {"status": "BLOCKED", "blocked_reasons": [str(ex)]}
                    current = self.tasks.snapshot(task_id)
                    self.tasks.transition(task_id, "DIAGNOSING", expected_revision=current["revision"], reason="Repeated failure requires root-cause diagnosis")
                    progress.update(phase="DIAGNOSING")
                    self._save("cycle", progress_key, progress)
                    diagnosis = self._call(task_id, lambda timeout: runner.run(adapter, self._request(task_id, "diagnostic", DIAGNOSTIC_SCHEMA,
                        {"instruction": "Find the root cause from actual proof and prior feedback; do not propose new authority.", "failure": verdict}),
                        timeout=timeout, cancel_requested=lambda: self._stop_requested(task_id)), task["contract"]["execution_policy"]["diagnostic_timeout_seconds"])
                    self._save("diagnostic", task_id, diagnosis)
                    if diagnosis["result"]["status"] != "COMPLETE" or diagnosis["result"]["payload"]["requires_new_approval"]:
                        progress.update(phase="NEEDS_APPROVAL")
                        self._save("cycle", progress_key, progress)
                        return {"status": "BLOCKED", "blocked_reasons": ["Diagnostic requires a revised approved proposal"]}
                    progress.update(phase="REJECTED")
                    self._save("cycle", progress_key, progress)
        incomplete = [s["id"] for s in ordered if (read_record(self.store.db, "stage", task_id + ":" + s["id"])["payload"] or {}).get("state") != "COMPLETED"]
        if incomplete:
            return {"status": "BLOCKED", "awaiting_approval": True, "blocked_reasons": ["Remaining stage obligations: " + ", ".join(incomplete)]}
        full_gate = GateService(self.store).gate(task_id)
        if full_gate["status"] == "APPROVED":
            return full_gate
        self.check(task_id)
        return self.verify(task_id)

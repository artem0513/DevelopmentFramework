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
from .profiles import validate_access, validate_evidence_spec, has_software, evaluate_contract_profiles
from .runner import Runner, verify_receipt
from .state import append_event, encode, hash_value, read_record, text, transaction, write_record
from .store import now
from .projects import _safe_state
from .native_paths import logical_path, state_io
from .tasks import TaskService
from .recovery import initial_history, bind_stage, canonical_failures, progress_tokens, group_tokens, original_case_progress


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STR = {"type": "string"}
STRINGS = {"type": "array", "items": STR}
AC_SCHEMA = _object({"criteria": {"type": "array", "items": _object({
    "id": STR, "status": {"type": "string", "enum": ["PASS", "FAIL", "UNVERIFIED", "BLOCKED", "NOT_APPLICABLE"]},
    "expected": STR, "evidence": STRINGS, "method": STR,
    "limitations": {**STRINGS, "description": "Nonblocking limits and explicitly pending later delivery obligations. They do not establish overall completion."},
    "missing_coverage": {**STRINGS, "description": "Unmet proof required by THIS criterion at THIS gate only. Any nonempty value blocks a mandatory criterion, including status PASS. Put nonblocking limits and later-phase delivery prerequisites in limitations."}})}})
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
    state_io(path.parent).mkdir(parents=True, exist_ok=True)
    physical = state_io(path)
    if physical.is_symlink() or (physical.exists() and physical.stat().st_nlink > 1):
        raise ValueError("Invalid execution lock")
    with physical.open("a+b") as stream:
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
            recovery_record, history, groups = self._recovery_context(db, task_id, stage_id)
            limits = history["hard_limits"]
            if limits.get("active_time_budget_seconds") is not None and value.get("active_seconds", 0) >= limits["active_time_budget_seconds"]:
                raise ValueError("Active time budget exhausted")
            key = task_id + ":" + stage_id
            count = max([value["stages"].get(key, 0)] + [history["groups"][name]["cycles"] for name in groups])
            if ((limits.get("max_stage_cycles") is not None and count >= limits["max_stage_cycles"])
                    or (limits.get("max_task_cycles") is not None and value["task_cycles"] >= limits["max_task_cycles"])):
                raise ValueError("Implementation cycle budget exhausted")
            if policy.get("recovery_mode", "fixed") == "progress" and any(history["groups"][name]["stopped"] for name in groups):
                raise ValueError("Corrective probe made no verified progress; automatic mutation is stopped")
            if any(history["groups"][name].get("probe_inflight") for name in groups):
                raise ValueError("Corrective probe is already inflight; reconcile its existing candidate before mutation")
            if any(history["groups"][name].get("diagnostic_inflight") for name in groups):
                raise ValueError("Diagnostic has no validated completion; fresh original progress is required before mutation")
            if any(history["groups"][name]["probe_pending"] and not self._probe_authorized(task_id, stage_id, name, history["groups"][name]) for name in groups):
                raise ValueError("Corrective probe requires a validated diagnostic authorization for the current evidence")
            if policy.get("recovery_mode", "fixed") == "progress" and any(
                    history["groups"][name]["stagnation"] >= policy.get("stagnation_limit", 3)
                    and not history["groups"][name]["probe_pending"] for name in groups):
                raise ValueError("Stagnation requires reassessment and diagnosis before another candidate")
            value["task_cycles"] += 1
            value["stages"][key] = count + 1
            candidate_id = uuid.uuid4().hex
            corrective = any(history["groups"][name]["probe_pending"] for name in groups)
            for name in groups:
                history["groups"][name]["cycles"] = count + 1
                if corrective:
                    history["groups"][name].update(probe_pending=False, probe_authorization=None, probe_inflight={"candidate_id": candidate_id,
                        "task_id": task_id, "stage_id": stage_id, "task_cycle": value["task_cycles"]})
            task = self.tasks.snapshot(task_id)
            cycle = read_record(db, "cycle", key)
            write_record(db, "cycle", key, {"phase": "IMPLEMENTING", "candidate_id": candidate_id,
                "corrective_probe": corrective, "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"]}, expected_revision=cycle["revision"])
            write_record(db, "recovery", root_id, history, expected_revision=recovery_record["revision"])
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        return {"task_cycles": value["task_cycles"], "stage_cycles": count + 1, "candidate_id": candidate_id, "corrective_probe": corrective}

    def _recovery_context(self, db, task_id, stage_id):
        root_id = self._root_task(task_id)
        record = read_record(db, "recovery", root_id)
        original = read_record(db, "task_version", root_id + ":1")["payload"]
        history = record["payload"] or initial_history((original or self.tasks.snapshot(root_id))["contract"])
        limits = history.setdefault("hard_limits", {})
        for key, value in self.tasks.snapshot(root_id)["contract"]["execution_policy"].items():
            if key in {"max_stage_cycles", "max_task_cycles", "max_diagnostic_passes", "active_time_budget_seconds"} and value is not None:
                limits[key] = min(value, limits.get(key, value))
        budget = (read_record(db, "budget", root_id)["payload"] or {})
        for name, group in history["groups"].items():
            group["cycles"] = max(group["cycles"], budget.get("stages", {}).get(root_id + ":" + name, 0))
            group["diagnostics"] = max(group["diagnostics"], budget.get("diagnostics", {}).get(root_id + ":" + name, 0))

        def bind(current_id, current_stage):
            contract = self.tasks.snapshot(current_id)["contract"]
            parent = contract.get("parent_task_id")
            inherited = bind(parent, contract["parent_stage_id"]) if parent else None
            return bind_stage(history, current_id, contract, current_stage, root_id=root_id, parent_groups=inherited)

        groups = bind(task_id, stage_id)
        if record["payload"] is None:
            # Old v2 coordinators stored raw stage names and reason hashes only.
            # Import conservatively once; missing old progress proof earns no reset.
            imported = {name: {"cycles": 0, "diagnostics": 0, "failures": 0} for name in history["groups"]}
            for field, target in (("stages", "cycles"), ("diagnostics", "diagnostics"), ("failures", "failures")):
                for old_key, count in budget.get(field, {}).items():
                    parts = old_key.split(":")
                    if len(parts) < 2:
                        continue
                    try:
                        if self._root_task(parts[0]) != root_id:
                            continue
                        inherited = bind(parts[0], parts[1])
                    except (ValueError, KeyError):
                        inherited = list(history["groups"])
                    for name in inherited:
                        imported[name][target] += count
            for name, counters in imported.items():
                group = history["groups"][name]
                group["cycles"] = max(group["cycles"], counters["cycles"])
                group["diagnostics"] = max(group["diagnostics"], counters["diagnostics"])
                group["legacy_failures"] = counters["failures"]
                group["stagnation"] = max(group["stagnation"], counters["failures"])
        return record, history, groups

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
        if self._stop_requested(task_id):
            raise ValueError("Task or ancestor is paused or cancelled")
        limit = root["contract"]["execution_policy"]["active_time_budget_seconds"]
        saved_limit = ((read_record(self.store.db, "recovery", root["task_id"])["payload"] or {}).get("hard_limits", {}).get("active_time_budget_seconds"))
        if saved_limit is not None:
            limit = min(limit, saved_limit) if limit is not None else saved_limit
        used = (read_record(self.store.db, "budget", root["task_id"])["payload"] or {}).get("active_seconds", 0)
        if limit is not None and used >= limit:
            raise ValueError("Active time budget exhausted")
        return min(timeout, limit - used) if limit is not None else timeout

    def _stop_requested(self, task_id):
        return any(task["state"] == "CANCELLED" or read_record(self.store.db, "stop", task["task_id"])["payload"]
                   for task in self._ancestry(task_id))

    def _ancestry(self, task_id):
        tasks, seen = [], set()
        while task_id:
            if task_id in seen:
                raise ValueError("Parent task cycle")
            seen.add(task_id)
            task = self.tasks.snapshot(task_id)
            tasks.append(task)
            task_id = task["contract"].get("parent_task_id")
        return tasks

    def _command_authority(self, task_id, spec, stage_id, *, allow_terminal=False):
        """Current declared recipe and the full inherited authority it relies on."""
        with transaction(self.store.db):
            task = self.tasks.require_approval(task_id, stage_id, allow_terminal=allow_terminal)
            stage = next(s for s in task["contract"]["stages"] if s["id"] == stage_id)
            plan = self.tasks.execution_plan(task_id)
            declared = [*stage_checks(task["contract"], plan["checks"], stage_id), *stage.get("actions", [])]
            if spec not in declared or not self._stage_permits_command(stage, spec):
                raise ValueError("Command differs from the current approved stage")
            if self._stop_requested(task_id):
                raise ValueError("Task or ancestor is paused or cancelled")
            return self._authority_binding(task_id)

    def _authority_binding(self, task_id):
        return {"plan_hash": self.tasks.execution_plan(task_id)["plan_hash"], "ancestry": [
            {"task_id": item["task_id"], "contract_hash": item["contract_hash"], "proposal_hash": item["proposal_hash"],
             "approval_revision": read_record(self.store.db, "approval", item["task_id"])["revision"],
             "approval_mode": "inherited" if item.get("inherited_approval") and not item.get("proposal") else "explicit"}
            for item in self._ancestry(task_id)]}

    def _stage_authority(self, task, stage):
        with transaction(self.store.db):
            current = self.tasks.require_approval(task["task_id"], stage["id"])
            if (current["contract_hash"] != task["contract_hash"] or current["proposal_hash"] != task["proposal_hash"]
                    or next(s for s in current["contract"]["stages"] if s["id"] == stage["id"]) != stage):
                raise ValueError("Approved stage context changed; discard the stale candidate")
            return self._authority_binding(task["task_id"])

    @staticmethod
    def _stage_permits_command(stage, spec):
        if "access" in spec and validate_access(spec["access"])["target"] not in stage["scope"]["resources"]:
            return False
        if "scope" in spec:
            from .tasks import _scope, _within
            if not _within(_scope(spec["scope"]), stage["scope"]):
                return False
        return True

    def _command_control(self, task_id, spec, stage_id, *, allow_terminal=False, prerequisite_binding=None, authority_binding=None):
        current_binding = self._command_authority(task_id, spec, stage_id, allow_terminal=allow_terminal)
        binding = current_binding if authority_binding is None else authority_binding
        if current_binding != binding:
            raise ValueError("Approved command authority changed during preparation")

        def current():
            return self._command_authority(task_id, spec, stage_id, allow_terminal=allow_terminal) == binding

        def cancelled():
            try:
                return not current()
            except ValueError:
                return True

        @contextmanager
        def launch_guard():
            # Serialize validation + OS process creation with revise/stop/cancel.
            # Never hold the DB writer while waiting for commands or auth.
            with transaction(self.store.db):
                if not current():
                    raise ValueError("Approved command authority changed before launch")
                # Check the verified inputs after authentication, immediately
                # before process creation. Own effects may legitimately change
                # them afterward, so cancellation keeps checking authority only.
                self._require_prerequisite_binding(task_id, stage_id, prerequisite_binding)
                yield

        return cancelled, launch_guard

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

    def _execute_command(self, task_id, spec, argv, log, timeout, stage_id=None, *, allow_terminal=False, prerequisite_binding=None, authority_binding=None):
        from .core import execute
        if stage_id is None:
            raise ValueError("Command requires its actual approved stage")
        stage = next(s for s in self.tasks.snapshot(task_id)["contract"]["stages"] if s["id"] == stage_id)
        if (stage["depends_on"] and spec in stage.get("actions", [])
                and list(argv) == spec["argv"] and prerequisite_binding is None):
            raise ValueError("Dependent effect requires current prerequisite evidence binding")
        cancel, launch_guard = self._command_control(task_id, spec, stage_id, allow_terminal=allow_terminal,
            prerequisite_binding=prerequisite_binding, authority_binding=authority_binding)
        if "access" not in spec:
            return execute(argv, self.store.repo, log, timeout, cancel_requested=cancel, launch_guard=launch_guard)
        self._access_stage(task_id, spec, stage_id, allow_terminal=allow_terminal)
        access = spec["access"]
        receipt = self._access_client().run(access["credential_ref"], access["target"], argv, self.store.repo,
            log, timeout=timeout, cancel_requested=cancel, launch_guard=launch_guard)
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
            with transaction(self.store.db):
                self.tasks.require_approval(task_id, stage_id)
                authority = self._authority_binding(task_id)
            spec = self._declared_access(task_id, stage_id, access)
            cancel, launch_guard = self._command_control(task_id, spec, stage_id, authority_binding=authority)
            return self._call(task_id, lambda timeout: self._access_client().ensure(
                access["credential_ref"], access["target"], timeout=timeout,
                cancel_requested=cancel, launch_guard=launch_guard), 60)

    def request_access(self, task_id, stage_id, access, path="/", method="GET"):
        with self._ownership(task_id):
            with transaction(self.store.db):
                self.tasks.require_approval(task_id, stage_id)
                authority = self._authority_binding(task_id)
            spec = self._declared_access(task_id, stage_id, access)
            cancel, launch_guard = self._command_control(task_id, spec, stage_id, authority_binding=authority)
            return self._call(task_id, lambda timeout: self._access_client().request(
                access["credential_ref"], access["target"], path=path, method=method, timeout=timeout,
                cancel_requested=cancel, launch_guard=launch_guard), 30)

    def _observe_action(self, task_id, stage, action, folder, *, allow_terminal=False, authority_binding=None):
        # Capture the observer's declaration before any authentication/process
        # work. Later aliases must retain this origin rather than relabel it.
        with transaction(self.store.db):
            current_authority = self._command_authority(task_id, action, stage["id"], allow_terminal=allow_terminal)
            authority = current_authority if authority_binding is None else authority_binding
            if current_authority != authority:
                raise ValueError("Approved observation authority changed during preparation")
            task = self.tasks.snapshot(task_id)
            declared_stage = next(item for item in task["contract"]["stages"] if item["id"] == stage["id"])
            if "scope" in stage and stage["scope"] != declared_stage["scope"]:
                raise ValueError("Observation stage scope differs from current declaration")
            provenance = {"version": 2, "kind": "observation", "task_id": task_id, "stage_id": stage["id"],
                "project_id": self.store.project_id, "workspace_id": self.store.workspace_id,
                "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"], "plan_hash": authority["plan_hash"],
                "action_sha256": hash_value(action), "argv_sha256": hash_value(action["argv"]),
                "observation_argv_sha256": hash_value(action["observation_argv"]), "declared_stage": declared_stage,
                "authority": authority, "execution_plan_checks": self.tasks.execution_plan(task_id)["checks"]}
        proof = self._call(task_id, lambda timeout: self._execute_command(task_id, action,
            action["observation_argv"], folder / (uuid.uuid4().hex + ".log"), timeout, stage["id"],
            allow_terminal=allow_terminal, authority_binding=authority), action.get("timeout", 300))
        if proof.get("exit_code") != 0 or proof.get("error") or proof.get("timed_out") or proof.get("cancelled"):
            raise ValueError("Resource observation failed; effect remains UNKNOWN")
        try:
            value = json.loads(state_io(proof["log"]).read_text(encoding="utf-8"))
        except ValueError:
            raise ValueError("Resource observation is not structured JSON; effect remains UNKNOWN") from None
        if (not isinstance(value, dict) or type(value.get("exists")) is not bool
                or (value["exists"] and (not isinstance(value.get("resource_id"), str)
                    or "[REDACTED]" in value["resource_id"]))):
            raise ValueError("Resource observation must prove exists and a nonsecret resource_id")
        observation = {"exists": value["exists"], "resource_id": value.get("resource_id"), "proof_ref": proof["log"] + "#sha256=" + proof["log_sha256"],
                       "action_hash": hash_value(action), "observing_task_id": task_id, "observing_stage_id": stage["id"],
                       "observing_workspace_id": self.store.workspace_id}
        if "absence_confirmed" in value:
            if type(value["absence_confirmed"]) is not bool:
                raise ValueError("Resource absence_confirmed must be boolean")
            observation["absence_confirmed"] = value["absence_confirmed"]
        if "provider_attempt_id" in value:
            text(value["provider_attempt_id"], "Provider attempt ID", limit=2000)
            if "[REDACTED]" in value["provider_attempt_id"]:
                raise ValueError("Provider attempt identity must be nonsecret and actual")
            observation["provider_attempt_id"] = value["provider_attempt_id"]
        if "access" in proof:
            observation["access"] = proof["access"]
        provenance["command_receipt"] = proof
        provenance["command_receipt_sha256"] = hash_value(proof)
        provenance["sha256"] = hash_value(provenance)
        observation["provenance"] = provenance
        return observation

    def _execute_actions(self, task_id, stage):
        """Probe actual state before every effect, including uncertain resumes."""
        from .core import execute
        from .operations import OperationLedger
        task = self.tasks.snapshot(task_id)
        authority = self._stage_authority(task, stage)
        ledger = OperationLedger(self.store)
        results = []
        for action in stage.get("actions", []):
            intent = {"project_id": self.store.project_id, "workspace_id": self.store.workspace_id, "action": action, "effect_scope": stage["scope"]}
            action_id = hash_value({"task": task_id, "stage": stage["id"], "action": action["id"]})
            prepared = ledger.prepare(task_id, stage["id"], action_id, intent)
            action_id = prepared["action_id"]
            folder = self.store.root / "tasks" / task_id / "actions" / action_id
            _safe_state(folder)
            state_io(folder).mkdir(parents=True, exist_ok=True)

            def observe():
                observation = self._observe_action(task_id, stage, action, folder, authority_binding=authority)
                if self._stage_authority(task, stage) != authority:
                    raise ValueError("Approved action authority changed during observation")
                return observation

            observation = observe()
            reconciled = ledger.reconcile(action_id, observation)
            if reconciled["state"] == "UNKNOWN":
                raise ValueError("External effect remains UNKNOWN; reconcile authoritative state before retry")
            if reconciled["state"] == "ABSENT":
                # A prior action or its observation may have changed a shared
                # prerequisite since the stage's candidate was checked.
                prerequisite_blockers, prerequisite_binding = self._revalidate_prerequisites(task, stage)
                if prerequisite_blockers:
                    raise ValueError("; ".join(prerequisite_blockers))
                action_source = source_snapshot(self.store, task["contract"], task_id=task_id)
                execution_context = {"version": 2, "task_id": task_id, "stage_id": stage["id"], "workspace_id": self.store.workspace_id,
                    "project_id": self.store.project_id, "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"],
                    "plan_hash": authority["plan_hash"], "action_sha256": hash_value(action), "argv_sha256": hash_value(action["argv"]),
                    "declared_stage": stage, "authority": authority,
                    "execution_plan_checks": self.tasks.execution_plan(task_id)["checks"],
                    "source_fingerprint_before": action_source["fingerprint"], "source_files": action_source["files"],
                    "source_files_sha256": hash_value(action_source["files"])}
                if len(encode(execution_context).encode()) > 131072:
                    raise ValueError("Action source evidence exceeds bound before submission")
                with transaction(self.store.db):
                    if self._stage_authority(task, stage) != authority:
                        raise ValueError("Approved action authority changed during prerequisite verification")
                    self._available(task_id, action.get("timeout", 300))
                    ledger.started(action_id)
                execution = self._call(task_id, lambda timeout: self._execute_command(task_id, action,
                    action["argv"], folder / (uuid.uuid4().hex + ".log"), timeout, stage["id"],
                    prerequisite_binding=prerequisite_binding, authority_binding=authority), action.get("timeout", 300))
                ledger.record_submission(action_id, execution, context=execution_context)
                observation = observe()
                ledger.reconcile(action_id, observation)
                if execution.get("exit_code") != 0 or not observation["exists"]:
                    raise ValueError("Infrastructure action did not establish its actual postcondition")
            results.append(observation)
        return results

    def record_failure(self, task_id, stage_id, reasons):
        return self._record_recovery(task_id, stage_id, reasons, failed=True)

    def _record_recovery(self, task_id, stage_id, reasons, *, failed):
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            recovery_record, history, groups = self._recovery_context(db, task_id, stage_id)
            key = ":".join(groups) + ":" + hash_value(canonical_failures(history, groups, reasons))
            inherited_failures = max(history["groups"][name].get("legacy_failures", 0) for name in groups)
            value["failures"][key] = value["failures"].get(key, inherited_failures) + int(failed)
            policy = self.tasks.snapshot(root_id)["contract"]["execution_policy"]
            tokens = self._verified_progress(task_id, stage_id, history, groups)
            newly_verified = sorted(set(tokens) - set(history["high_water"]))
            history["high_water"] = sorted(set(history["high_water"]) | set(tokens))
            for name in groups:
                group = history["groups"][name]
                if set(newly_verified) & group_tokens(name, group):
                    group.update(stagnation=0, probe_pending=False, probe_inflight=None, diagnostic_inflight=None,
                                 probe_authorization=None, stopped=False)
                    group["progress_generation"] = group.get("progress_generation", 0) + 1
                elif failed:
                    group["stagnation"] += 1
                    if group["probe_pending"] or group.get("probe_inflight"):
                        group.update(stopped=True, probe_pending=False, probe_inflight=None, probe_authorization=None)
                elif not failed:
                    group["probe_inflight"] = None
            stagnation = max(history["groups"][name]["stagnation"] for name in groups)
            stopped = any(history["groups"][name]["stopped"] for name in groups)
            history["events"].append({"task_id": task_id, "stage_id": stage_id, "reasons": reasons,
                "failure_key": key, "newly_verified": newly_verified, "stagnation": stagnation, "stopped": stopped, "timestamp": now()})
            if newly_verified:
                history["events"][-1]["proof"] = {"checks": read_record(db, "checks", task_id)["payload"],
                    "verification": {role: read_record(db, "verification", task_id + ":" + role)["payload"] for role in ("ac", "review")}}
            write_record(db, "recovery", root_id, history, expected_revision=recovery_record["revision"])
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        diagnostic = (stagnation >= policy.get("stagnation_limit", 3) and not stopped if policy.get("recovery_mode", "fixed") == "progress"
                      else value["failures"][key] >= policy["repeat_failure_limit"])
        return {"diagnostic_required": diagnostic, "repetitions": value["failures"][key],
                "stagnation": stagnation, "stopped": stopped, "newly_verified": newly_verified}

    def _verified_progress(self, task_id, stage_id, history, groups):
        """Credit only fresh evaluated original checks and independent judgments."""
        task = self.tasks.snapshot(task_id)
        contract, plan = task["contract"], self.tasks.execution_plan(task_id)
        bundle = read_record(self.store.db, "checks", task_id)["payload"] or {}
        source = source_snapshot(self.store, contract, task_id=task_id)
        if (bundle.get("fingerprint") != source["fingerprint"] or bundle.get("contract_hash") != task["contract_hash"]
                or bundle.get("plan_hash") != plan["plan_hash"] or bundle.get("workspace_id") != self.store.workspace_id
                or bundle.get("source_changed_during_checks")):
            return []
        specs = stage_checks(contract, plan["checks"], stage_id)
        profile = evaluate_contract_profiles(contract, specs, bundle.get("results", []), stage_id=stage_id)
        checked = {key for key, result in profile["by_id"].items() if result["status"] == "PASS"}
        case_tokens = original_case_progress(self.store, self._root_task(task_id), task_id, history, groups,
                                            contract, stage_id, bundle, source, checked)
        bindings = {"fingerprint": source["fingerprint"], "contract_sha256": task["contract_hash"],
            "proposal_sha256": task["proposal_hash"], "plan_sha256": plan["plan_hash"],
            "check_sha256": hash_value(bundle), "workspace_id": self.store.workspace_id}
        roles, execution_ids, session_ids = {}, set(), set()
        implementation = read_record(self.store.db, "implementation", task_id)["payload"] or {}
        for role in ("ac", "review"):
            receipt = read_record(self.store.db, "verification", task_id + ":" + role)["payload"] or {}
            try:
                if not Path(receipt["artifact_root"]).resolve().is_relative_to(self.store.root.resolve()):
                    continue
                verified = verify_receipt(receipt, expected_bindings=bindings)
                execution_id, session_id = verified["execution_id"], verified["result"].get("session_id")
                if (verified["evidence_kind"] != "independent_cli" or verified["result"].get("status") != "COMPLETE"
                        or execution_id in execution_ids or execution_id == implementation.get("execution_id")
                        or (session_id and session_id in session_ids)):
                    continue
                execution_ids.add(execution_id)
                if session_id:
                    session_ids.add(session_id)
                roles[role] = verified["result"]["payload"]
            except (ValueError, OSError, KeyError, TypeError):
                continue
        accepted = set()
        criteria = roles.get("ac", {}).get("criteria", [])
        if len({item.get("id") for item in criteria}) == len(criteria):
            for ac in contract["acceptance"]:
                matches = [item for item in criteria if item.get("id") == ac["id"]]
                if len(matches) == 1:
                    item = matches[0]
                    if (item.get("status") == "PASS" and item.get("expected") == ac["expected"] and item.get("evidence")
                            and item.get("method") and not item.get("missing_coverage")
                            and set(ac["evidence_ids"]).issubset(checked)):
                        accepted.add(ac["id"])
        review = roles.get("review", {})
        clean_review = bool(review.get("summary")) and isinstance(review.get("findings"), list) and all(
            isinstance(finding, dict) and finding.get("severity") == "P3" for finding in review["findings"])
        return sorted(set(progress_tokens(history, groups, contract, stage_id, checked, accepted, clean_review)) | case_tokens)

    def reserve_diagnostic(self, task_id, stage_id):
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record = read_record(db, "budget", root_id)
            value = record["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
            key = task_id + ":" + stage_id
            policy = self.tasks.snapshot(root_id)["contract"]["execution_policy"]
            recovery_record, history, groups = self._recovery_context(db, task_id, stage_id)
            count = max([value["diagnostics"].get(key, 0)] + [history["groups"][name]["diagnostics"] for name in groups])
            limit = history["hard_limits"].get("max_diagnostic_passes")
            if limit is not None and count >= limit:
                raise ValueError("diagnostic budget exhausted")
            if any(history["groups"][name].get("diagnostic_inflight") for name in groups):
                raise ValueError("Unresolved diagnostic reservation requires fresh original progress")
            reservation = {"id": uuid.uuid4().hex, "task_id": task_id, "stage_id": stage_id,
                           "bindings": self._diagnostic_bindings(task_id), "status": "RUNNING",
                           "generations": {name: history["groups"][name].get("progress_generation", 0) for name in groups}}
            if policy.get("recovery_mode", "fixed") == "progress":
                if any(history["groups"][name]["stopped"] or history["groups"][name]["diagnosed_generation"] == history["groups"][name].get("progress_generation", 0) for name in groups):
                    raise ValueError("No verified progress since the prior diagnostic; automatic mutation is stopped")
                for name in groups:
                    group = history["groups"][name]
                    group.update(diagnosed_generation=group.get("progress_generation", 0))
            for name in groups:
                history["groups"][name].update(diagnostics=count + 1, diagnostic_inflight=reservation,
                                               probe_pending=False, probe_authorization=None)
            value["diagnostics"][key] = count + 1
            write_record(db, "recovery", root_id, history, expected_revision=recovery_record["revision"])
            write_record(db, "budget", root_id, value, expected_revision=record["revision"])
        return value["diagnostics"][key]

    def _diagnostic_bindings(self, task_id):
        task = self.tasks.snapshot(task_id)
        checks = read_record(self.store.db, "checks", task_id)["payload"]
        return {"fingerprint": source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"],
            "contract_sha256": task["contract_hash"], "proposal_sha256": task["proposal_hash"] or "unapproved",
            "plan_sha256": self.tasks.execution_plan(task_id)["plan_hash"],
            "check_sha256": hash_value(checks) if checks else "missing", "workspace_id": self.store.workspace_id}

    @staticmethod
    def _diagnostic_context(reservation):
        return {"reservation_id": reservation.get("id"), "task_id": reservation.get("task_id"),
            "stage_id": reservation.get("stage_id"), "groups": sorted(reservation.get("generations", {})),
            "generations": reservation.get("generations", {})}

    def _diagnostic_supported(self, receipt, bindings, reservation_context):
        try:
            if (not reservation_context or not reservation_context.get("reservation_id") or receipt.get("role") != "diagnostic"
                    or not Path(receipt["artifact_root"]).resolve().is_relative_to(self.store.root.resolve())):
                return False
            verified = verify_receipt(receipt, expected_bindings=bindings)
            captured = receipt["artifacts"]["request"]
            data = Path(captured["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != captured["sha256"]:
                return False
            request = json.loads(data)
            if (request.get("role") != "diagnostic" or request.get("schema") != DIAGNOSTIC_SCHEMA
                    or request.get("handoff", {}).get("diagnostic_reservation") != reservation_context):
                return False
            result = verified["result"]
            payload = result.get("payload", {})
            return (result.get("status") == "COMPLETE" and bool(payload.get("cause", "").strip())
                    and bool(payload.get("recommendation", "").strip()) and payload.get("requires_new_approval") is False)
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            return False

    def _probe_authorized(self, task_id, stage_id, name, group):
        permission = group.get("probe_authorization") or {}
        return (permission.get("task_id") == task_id and permission.get("stage_id") == stage_id
                and permission.get("generations", {}).get(name) == group.get("progress_generation", 0)
                and permission.get("bindings") == self._diagnostic_bindings(task_id)
                and self._diagnostic_supported(permission.get("receipt", {}), permission["bindings"], permission.get("reservation")))

    def _complete_diagnostic(self, task_id, stage_id, diagnosis):
        """Only captured supported completion can mint one durable probe permission."""
        root_id = self._root_task(task_id)
        with transaction(self.store.db) as db:
            record, history, groups = self._recovery_context(db, task_id, stage_id)
            reservations = [history["groups"][name].get("diagnostic_inflight") or {} for name in groups]
            reservation = reservations[0]
            receipt = diagnosis.get("receipt", {})
            supported = (bool(reservation.get("id")) and all(item.get("id") == reservation["id"] for item in reservations)
                and reservation.get("task_id") == task_id and reservation.get("stage_id") == stage_id
                and reservation.get("status") == "RUNNING" and reservation.get("bindings") == self._diagnostic_bindings(task_id)
                and all(reservation.get("generations", {}).get(name) == history["groups"][name].get("progress_generation", 0) for name in groups)
                and self._diagnostic_supported(receipt, reservation["bindings"], self._diagnostic_context(reservation)))
            permission = {"task_id": task_id, "stage_id": stage_id, "reservation_id": reservation.get("id"),
                "bindings": reservation.get("bindings"), "receipt": receipt,
                "generations": reservation.get("generations", {}), "reservation": self._diagnostic_context(reservation)}
            for name in groups:
                group = history["groups"][name]
                if supported:
                    group.update(diagnostic_inflight=None, probe_pending=True, probe_authorization=permission)
                else:
                    group.update(diagnostic_inflight={**reservation, "status": "FAILED"}, probe_pending=False,
                                 probe_authorization=None, stopped=True)
            history["events"].append({"task_id": task_id, "stage_id": stage_id, "timestamp": now(),
                "diagnostic_reservation": reservation.get("id"), "supported": bool(supported), "receipt": receipt})
            # Permission and its resumable coordinator phase are one commit.
            # A crash after completion must not leave valid permission hidden
            # behind the earlier DIAGNOSING checkpoint.
            cycle_key = task_id + ":" + stage_id
            cycle = read_record(db, "cycle", cycle_key)
            task = self.tasks.snapshot(task_id)
            write_record(db, "cycle", cycle_key, {**(cycle["payload"] or {}),
                "phase": "REJECTED" if supported else "NEEDS_APPROVAL",
                "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"],
                "diagnostic_reservation_id": reservation.get("id")}, expected_revision=cycle["revision"])
            write_record(db, "recovery", root_id, history, expected_revision=record["revision"])
        return bool(supported)

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
        with transaction(self.store.db):
            task = self.tasks.snapshot(task_id)
            authority = self._authority_binding(task_id)
        allowed = []
        for stage in task["contract"]["stages"]:
            if stage_id is not None and stage["id"] != stage_id:
                continue
            try:
                self.tasks.require_approval(task_id, stage["id"], allow_terminal=task["state"] == "COMPLETED")
            except ValueError:
                if stage_id is not None:
                    raise
                continue
            allowed.append(stage["id"])
        if not allowed:
            raise ValueError("Checks require current proposal approval")
        if task["state"] == "COMPLETED":
            # Credential rotation permits fresh observations/checks, never replay
            # a completed resource mutation or reopen its business authority.
            from .operations import OperationLedger
            ledger = OperationLedger(self.store)
            for stage in task["contract"]["stages"]:
                if stage["id"] not in allowed:
                    continue
                for action in stage.get("actions", []):
                    if "access" not in action:
                        continue
                    action_id = hash_value({"task": task_id, "stage": stage["id"], "action": action["id"]})
                    intent = {"project_id": self.store.project_id, "workspace_id": self.store.workspace_id, "action": action, "effect_scope": stage["scope"]}
                    operation = ledger.lookup(task_id, stage["id"], action_id, intent)
                    if operation:
                        action_id = operation["action_id"]
                        folder = self.store.root / "tasks" / task_id / "actions" / action_id
                        _safe_state(folder)
                        state_io(folder).mkdir(parents=True, exist_ok=True)
                        ledger.reconcile(action_id, self._observe_action(task_id, stage, action, folder,
                            allow_terminal=True, authority_binding=authority))
        plan = self.tasks.execution_plan(task_id)
        before = source_snapshot(self.store, task["contract"], task_id=task_id)
        folder = self.store.root / "tasks" / task_id / "checks" / uuid.uuid4().hex
        _safe_state(folder)
        state_io(folder).mkdir(parents=True)
        results = []
        check_stages = {}
        for approved_stage in allowed:
            stage = next(s for s in task["contract"]["stages"] if s["id"] == approved_stage)
            for raw in stage_checks(task["contract"], plan["checks"], approved_stage):
                if self._stage_permits_command(stage, raw):
                    check_stages.setdefault(raw["id"], approved_stage)
                elif stage_id is not None:
                    raise ValueError("Check access or scope exceeds the explicitly selected approved stage")
        for raw in plan["checks"]:
            if raw["id"] not in check_stages:
                continue
            actual_stage = check_stages[raw["id"]]
            self.tasks.require_approval(task_id, actual_stage, allow_terminal=task["state"] == "COMPLETED")
            spec = validate_evidence_spec(raw, task["contract"]["profile"])
            if spec["kind"] == "artifact":
                path = _target(self.store.repo.resolve(), spec["path"])
                result = {"id": spec["id"], "kind": "artifact", "path": str(path),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
            elif spec["kind"] == "manual":
                result = read_record(self.store.db, "manual_evidence", task_id + ":" + spec["id"])["payload"] or {"id": spec["id"], "kind": "manual", "confirmed": False}
            else:
                result = self._call(task_id, lambda timeout: self._execute_command(task_id, spec, spec["argv"],
                    folder / (spec["id"] + ".log"), timeout, actual_stage, allow_terminal=task["state"] == "COMPLETED",
                    authority_binding=authority), spec.get("timeout", 300))
                result.update(id=spec["id"], kind=spec["kind"], parser=spec.get("parser"), configured_argv=spec["argv"])
            results.append(result)
        after = source_snapshot(self.store, task["contract"], task_id=task_id)
        bundle = {"kind": "checks", "task_id": task_id, "fingerprint": before["fingerprint"], "workspace_id": self.store.workspace_id,
                  "contract_hash": task["contract_hash"], "plan_hash": plan["plan_hash"], "results": results,
                  "source_changed_during_checks": before["fingerprint"] != after["fingerprint"], "timestamp": now()}
        return self._save("checks", task_id, bundle)

    def _capture_stage(self, task_id, stage_id, verdict):
        task = self.tasks.snapshot(task_id)
        stage = next(item for item in task["contract"]["stages"] if item["id"] == stage_id)
        saved = read_record(self.store.db, "stage", task_id + ":" + stage_id)
        previous = saved["payload"] or {}
        if previous.get("state") == "COMPLETED" and all(previous.get(key) == task[key] for key in ("contract_hash", "proposal_hash")):
            raise ValueError("Existing completed stage history cannot be replaced implicitly")
        kind, key = "stage_implementation", task_id + ":" + stage_id
        selected = read_record(self.store.db, kind, key)
        if selected["payload"] is None:
            kind, key = "implementation", task_id
            selected = read_record(self.store.db, kind, key)
        receipt = selected["payload"]
        if not receipt:
            raise ValueError("Stage implementation receipt is unavailable")
        expected = {"contract_sha256": task["contract_hash"], "proposal_sha256": task["proposal_hash"],
            "plan_sha256": self.tasks.execution_plan(task_id)["plan_hash"], "workspace_id": self.store.workspace_id}
        root = logical_path(receipt["artifact_root"])
        if not root.is_relative_to(logical_path(self.store.root / "tasks" / task_id / "sessions")):
            raise ValueError("Stage implementation receipt escapes this task's sessions")
        if any(receipt.get("bindings", {}).get(name) != value for name, value in expected.items()):
            raise ValueError("Implementation stage receipt bindings are stale")
        verify_receipt(receipt, require_current_policy=False)
        data = state_io(receipt["artifacts"]["request"]["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != receipt["artifacts"]["request"]["sha256"]:
            raise ValueError("Stage implementation request changed after verification")
        request = json.loads(data)
        captured = request.get("handoff", {}).get("stage", {})
        if (receipt.get("role") != "implementation" or request.get("role") != "implementation"
                or captured.get("id") != stage_id or ("scope" in captured and captured["scope"] != stage["scope"])
                or request["project"].get("project_id") != self.store.project_id
                or logical_path(request["project"]["root"]) != logical_path(self.store.repo)):
            raise ValueError("Stage implementation receipt has another stage/project identity")
        # Artifact IO/verification stays outside the writer. Publish this exact
        # prospective capture only if none of its selected identities changed.
        with transaction(self.store.db):
            current = self.tasks.snapshot(task_id)
            if (any(current[name] != task[name] for name in ("contract_hash", "proposal_hash"))
                    or self.tasks.execution_plan(task_id)["plan_hash"] != expected["plan_sha256"]
                    or read_record(self.store.db, kind, key) != selected
                    or read_record(self.store.db, "stage", task_id + ":" + stage_id)["revision"] != saved["revision"]):
                raise ValueError("Stage capture identities changed during verification")
            self._save("stage", task_id + ":" + stage_id, {"state": "COMPLETED", "contract_hash": task["contract_hash"],
                "proposal_hash": task["proposal_hash"], "fingerprint": verdict["fingerprint"], "gate": verdict, "completed_at": now(),
                "proof": {"version": 1, "checks": read_record(self.store.db, "checks", task_id)["payload"],
                    "roles": {"implementation": receipt, **{role: read_record(self.store.db, "verification", task_id + ":" + role)["payload"]
                        for role in ("ac", "review")}}}})

    def _stage_history(self, task):
        history, missing = {"historical_only": True, "stages": []}, []
        for stage in task["contract"]["stages"]:
            saved = read_record(self.store.db, "stage", task["task_id"] + ":" + stage["id"])["payload"]
            if not saved or saved.get("state") != "COMPLETED":
                continue
            try:
                supersession = None
                if saved.get("contract_hash") != task["contract_hash"] or saved.get("proposal_hash") != task["proposal_hash"]:
                    from .action_evidence import _superseded_stage
                    original_stage, supersession = _superseded_stage(self.store, task, stage["id"],
                        saved.get("contract_hash"), saved.get("proposal_hash"))
                proof = saved["proof"]
                if proof.get("version") != 1 or not saved.get("completed_at"):
                    raise ValueError("stage has no prospective proof capture")
                checks = proof["checks"]
                bindings = {"fingerprint": checks["fingerprint"], "contract_sha256": saved["contract_hash"],
                    "proposal_sha256": saved["proposal_hash"], "plan_sha256": checks["plan_hash"],
                    "check_sha256": hash_value(checks), "workspace_id": self.store.workspace_id}
                entry = {"stage_id": stage["id"], "completed_at": saved["completed_at"], "contract_hash": saved["contract_hash"],
                    "proposal_hash": saved["proposal_hash"], "gate": saved["gate"], "checks": checks, "roles": {}, "observations": [],
                    "current_identity": supersession is None, **({"supersession": supersession} if supersession else {})}
                observations = {}
                for role in ("implementation", "ac", "review"):
                    receipt = proof["roles"][role]
                    root = Path(receipt["artifact_root"])
                    _safe_state(root)
                    if not root.resolve().is_relative_to(self.store.root.resolve()):
                        raise ValueError("historical receipt escapes project state")
                    verified = verify_receipt(receipt, expected_bindings=bindings if role != "implementation" else None,
                        require_current_policy=False)
                    if any(receipt["bindings"].get(key) != bindings[key] for key in ("contract_sha256", "proposal_sha256", "workspace_id", "plan_sha256")):
                        raise ValueError("historical receipt identity differs")
                    request = json.loads(Path(receipt["artifacts"]["request"]["path"]).read_bytes())
                    if request.get("role") != role:
                        raise ValueError("historical role mismatch")
                    handoff = request.get("handoff", {})
                    captured_stage = handoff.get("stage", {}).get("id") if role == "implementation" else handoff.get("stage_id")
                    if captured_stage != stage["id"]:
                        raise ValueError("historical stage boundary mismatch")
                    if supersession:
                        supplied = {item["path"]: item for item in request["source_packet"]["files"]}
                        contract = json.loads(supplied["_ase_context/contract.json"]["content"])
                        plan = json.loads(supplied["_ase_context/execution_plan.json"]["content"])
                        if (hash_value(contract) != saved["contract_hash"] or plan["plan_hash"] != checks["plan_hash"]
                                or hash_value(plan["checks"]) != checks["plan_hash"]
                                or (role == "implementation" and "scope" in handoff.get("stage", {})
                                    and handoff["stage"]["scope"] != original_stage["scope"])
                                or request["project"].get("project_id") != self.store.project_id
                                or logical_path(request["project"]["root"]) != logical_path(self.store.repo)):
                            raise ValueError("superseded history origin differs")
                    entry["roles"][role] = {"execution_id": verified["execution_id"], "evidence_kind": verified["evidence_kind"],
                        "bindings": receipt["bindings"], "started_at_utc": receipt.get("started_at_utc"), "ended_at_utc": receipt.get("ended_at_utc"),
                        "session_id": receipt.get("session_id"), "receipt_sha256": hash_value(receipt),
                        "policy_current": verified['policy_current'], "policy_status": verified['policy_status']}
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
                entry['policy_current'] = all(entry['roles'][role]['policy_current'] for role in ('ac', 'review'))
                entry["semantic_approval"] = supersession is None and saved["gate"].get("status") == "APPROVED" and entry['policy_current'] and all(entry["roles"][role]["evidence_kind"] == "independent_cli" for role in ("ac", "review"))
                if len(encode({"stages": history["stages"] + [entry]}).encode("utf-8")) > 262144:
                    raise ValueError("stage history exceeds bounded packet budget")
                history["stages"].append(entry)
            except (ValueError, OSError, KeyError, TypeError) as error:
                missing.append("Stage history unavailable: " + stage["id"] + ": " + str(error))
        return history, missing

    def _packet(self, task_id, stage_id=None):
        from .source_coverage import source_availability, availability_gaps, source_bytes, bound_representation, read_persisted_intake
        task = self.tasks.snapshot(task_id)
        row = self.store.db.execute("SELECT payload FROM task_discoveries WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        discovery = json.loads(row[0]) if row else None
        intake_state = read_persisted_intake(discovery)
        availability = source_availability(self.store, task["contract"], discovery, stage_id)
        policies = {item["path"]: item for item in availability}
        representations = {item["artifact_path"]: item for item in (discovery or {}).get("representations", [])}
        plan = self.tasks.execution_plan(task_id)
        stage = next((item for item in task["contract"]["stages"] if item["id"] == stage_id), None)
        if stage_id is not None and stage is None:
            raise ValueError("Unknown source packet stage")
        must_review = {Path(item["path"]).as_posix() for item in stage_checks(task["contract"], plan["checks"], stage_id)
                       if item["kind"] == "artifact" and item.get("required", True)}

        def source_policy(path):
            matching = [item for resource, item in policies.items() if Path(path).is_relative_to(Path(resource))]
            return max(matching, key=lambda item: len(Path(item["path"]).parts)) if matching else None

        def coverage_required(path):
            if body_omission(path) is not None:
                return False
            policy = source_policy(path)
            return path in must_review or policy is None or (policy["relevant"] and (policy["required"] or policy["role"] in {"output", "workspace", "representation"}))

        def direct_body_omission(path):
            if path in must_review:
                return None
            policy = source_policy(path)
            if policy is not None:
                if not policy["relevant"]:
                    return "OMITTED_IRRELEVANT_STAGE"
                if policy["role"] == "input" and not policy["required"]:
                    return "OMITTED_OPTIONAL_INPUT"
            return None

        def body_omission(path):
            if path in must_review:
                return None
            owners = [artifact for artifact, declaration in representations.items() if declaration["path"] == path]
            if owners:
                policy = source_policy(path)
                if policy is not None and policy["role"] != "representation" and direct_body_omission(path) is None:
                    return None  # Independently required as a source or output.
                decisions = [direct_body_omission(artifact) for artifact in owners]
                if all(decisions):
                    return "OMITTED_OPTIONAL_INPUT" if "OMITTED_OPTIONAL_INPUT" in decisions else "OMITTED_IRRELEVANT_STAGE"
                return None  # At least one included artifact needs this body.
            return direct_body_omission(path)

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
            review_scope = stage["scope"] if stage is not None else task["contract"]["scope"]
            if set(review_scope["effects"]) & {"file_write", "command"}:
                must_review.update(path for path in changed if any(Path(path).is_relative_to(Path(resource)) for resource in review_scope["resources"]))
        must_review.update(item["path"] for artifact, item in representations.items() if artifact in must_review)
        files = []
        missing = [gap["id"] + ": " + gap["text"] for gap in [*availability_gaps(availability), *intake_state["compatibility_gaps"]]]
        for path, observation in snapshot.get("unavailable", {}).items():
            availability.append({"path": path, **observation, "reason": "Source snapshot could not read this declared resource"})
            if coverage_required(path):
                missing.append(path + ": source snapshot unavailable (" + observation["error"] + ")")
        representation_metadata = []
        for artifact, declaration in representations.items():
            omission = body_omission(artifact)
            if omission:
                artifact_hash = snapshot["files"].get(artifact, "").removesuffix(":x")
                readable_hash = snapshot["files"].get(declaration["path"], "").removesuffix(":x")
                current = artifact_hash == declaration["artifact_sha256"] and readable_hash == declaration["sha256"]
                representation_metadata.append({**declaration, "body_status": omission,
                                                "observed_artifact_sha256": artifact_hash or None, "observed_representation_sha256": readable_hash or None,
                                                "integrity": "CURRENT_SNAPSHOT_HASHES" if current else "STALE_OR_UNAVAILABLE_SNAPSHOT_BINDING",
                                                "limitation": "Artifact/representation bodies were omitted from review; the declared binding and current source hashes remain visible"})
        omitted_sources = []
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
                omission = body_omission(path)
                if omission:
                    policy = source_policy(path)
                    observed_hash = snapshot["files"].get(path)
                    omitted_sources.append({"path": path, "sha256": observed_hash,
                                            "status": "AVAILABLE" if observed_hash is not None else "UNAVAILABLE",
                                            "body_status": omission, "required": policy["required"], "relevant": policy["relevant"],
                                            "blocking_stage_ids": policy["blocking_stage_ids"], "reason": policy["reason"],
                                            "limitation": "Body was not supplied or reviewed in this packet; current source hashes and declared stage impact remain visible"})
                    continue
                if path in representations:
                    represented = bound_representation(self.store.repo, representations[path])
                    if not any(item["path"] == represented["path"] for item in files):
                        files.append(represented)
                    representation_metadata.append({**representations[path], "integrity": "Current artifact and readable representation bytes match both declared hashes",
                                                    "limitation": "Representation adequacy requires independent domain judgment; native binary bytes are not interpreted by this transport"})
                    continue
                data = source_bytes(self.store.repo, path)
                content = data.decode("utf-8")
                if "\0" in content:
                    raise ValueError("source contains binary NUL bytes")
                if not any(item["path"] == path for item in files):
                    files.append({"path": path, "content": content, "sha256": hashlib.sha256(data).hexdigest()})
            except (UnicodeError, OSError, ValueError) as ex:
                if coverage_required(path):
                    missing.append(path + ": " + str(ex))
                else:
                    availability.append({"path": path, "status": "UNCOVERED", "required": False,
                                         "limitation": str(ex), "reason": "Optional or outside the current stage's source requirement"})
        historical = read_record(self.store.db, "baseline_implementation", task_id)["payload"]
        if historical and changes:
            try:
                if not Path(historical["artifact_root"]).resolve().is_relative_to(self.store.root.resolve()):
                    raise ValueError("Historical source capture escapes project state")
                verify_receipt(historical, require_current_policy=False)
                original = json.loads(Path(historical["artifacts"]["request"]["path"]).read_bytes())
                changed_paths = {entry["path"] for entry in changes["changes"]}
                for before in original["source_packet"]["files"]:
                    if before["path"] in changed_paths and body_omission(before["path"]) is None:
                        files.append({**before, "path": "_ase_before/" + before["path"]})
            except (ValueError, OSError, KeyError, TypeError) as ex:
                missing.append("Historical source capture unavailable: " + str(ex))
        stage_history, history_missing = self._stage_history(task)
        missing.extend(history_missing)
        from .action_evidence import action_packet_evidence
        action_files, action_missing = action_packet_evidence(self.store, task, plan, snapshot, stage_id=stage_id)
        files.extend(action_files)
        missing.extend(action_missing)
        for name, data in (("contract", task["contract"]), ("execution_plan", plan), ("source_changes", changes), ("stage_history", stage_history),
                           ("discovery", discovery), ("intake_validation", intake_state["validation"]), ("source_availability", availability), ("artifact_representations", representation_metadata), ("omitted_sources", omitted_sources),
                           ("actual_checks", read_record(self.store.db, "checks", task_id)["payload"])):
            content = encode(data)
            files.append({"path": "_ase_context/" + name + ".json", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        check = read_record(self.store.db, "checks", task_id)["payload"]
        for result in (check or {}).get("results", []):
            if result.get("log"):
                try:
                    log = logical_path(result["log"])
                    if not log.is_relative_to(self.store.root.resolve()):
                        raise ValueError("Log is outside project state")
                    physical = state_io(log)
                    if not physical.is_file():
                        raise ValueError("Log is unavailable")
                    content = physical.read_bytes().decode("utf-8", errors="replace")
                except (OSError, ValueError):
                    missing.append("Actual check log unavailable: " + result["id"])
                    continue
                files.append({"path": "_ase_context/logs/" + result["id"] + ".txt", "content": content,
                              "sha256": hashlib.sha256(content.encode()).hexdigest()})
        packet = {"requirements": [task["contract"]["request"], *[ac["id"] + ": " + ac["expected"] for ac in task["contract"]["acceptance"]]],
                "files": files, "coverage": {"scope": "Current stage source plus task contract and actual check logs; known irrelevant/optional bodies are explicitly disclosed in omitted_sources; undeclared dependencies are not proven complete",
                                               "included": [f["path"] for f in files], "missing": missing,
                                               "omitted": [item["path"] for item in omitted_sources]}}
        from .protocol import validate_packet
        return validate_packet(packet)

    def _request(self, task_id, role, schema, handoff=None):
        task = self.tasks.snapshot(task_id)
        plan = self.tasks.execution_plan(task_id)
        check = read_record(self.store.db, "checks", task_id)["payload"]
        source = source_snapshot(self.store, task["contract"], task_id=task_id)
        handoff = dict(handoff or {})
        if 'runtime_policy' in handoff:
            raise ValueError('runtime_policy is reserved for the trusted host')
        from .runtime_policy import current_policy
        handoff['runtime_policy'] = current_policy(role)
        if role == "diagnostic":
            history = read_record(self.store.db, "recovery", self._root_task(task_id))["payload"] or {}
            reservations = {}
            for group in history.get("groups", {}).values():
                reservation = group.get("diagnostic_inflight") or {}
                if (reservation.get("status") == "RUNNING" and reservation.get("task_id") == task_id
                        and (handoff.get("stage_id") is None or handoff["stage_id"] == reservation.get("stage_id"))):
                    reservations[reservation["id"]] = reservation
            if len(reservations) != 1:
                raise ValueError("Diagnostic request requires one current reservation and stage")
            handoff["diagnostic_reservation"] = self._diagnostic_context(next(iter(reservations.values())))
        return {"role": role, "project": {"root": str(self.store.repo), "project_id": self.store.project_id}, "schema": schema,
                "bindings": {"fingerprint": source["fingerprint"], "contract_sha256": task["contract_hash"], "proposal_sha256": task["proposal_hash"] or "unapproved",
                             "plan_sha256": plan["plan_hash"], "check_sha256": hash_value(check) if check else "missing", "workspace_id": self.store.workspace_id},
                "source_packet": self._packet(task_id, stage_id=handoff.get("stage_id") or handoff.get("stage", {}).get("id")), "handoff": handoff}

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

    def _revalidate_prerequisites(self, task, stage):
        """Historical completion cannot authorize a new dependent effect.

        Reassess the complete dependency chain against current checks and fresh
        independent judgment. Never reserve/replay prerequisite implementation
        or actions. Keeping the historical marker allows a later explicit resume
        to reassess repaired artifacts without repeating completed mutations.
        Return blockers and the common source binding from successful checks
        and judgments, never a newly sampled baseline after verification.
        """
        by_id = {item["id"]: item for item in task["contract"]["stages"]}
        ordered, selected = [], set()
        def include(stage_id):
            if stage_id in selected:
                return
            selected.add(stage_id)
            for dependency in by_id[stage_id]["depends_on"]:
                include(dependency)
            ordered.append(stage_id)
        for stage_id in stage["depends_on"]:
            include(stage_id)
        binding = None
        for stage_id in ordered:
            try:
                proof = read_record(self.store.db, "stage", task["task_id"] + ":" + stage_id)["payload"] or {}
                if (proof.get("state") != "COMPLETED" or proof.get("contract_hash") != task["contract_hash"]
                        or proof.get("proposal_hash") != task["proposal_hash"]):
                    raise ValueError("Current completed stage proof is missing")
                self.tasks.require_approval(task["task_id"], stage_id)
                checks = self.check(task["task_id"], stage_id)
                specs = stage_checks(task["contract"], self.tasks.execution_plan(task["task_id"])["checks"], stage_id)
                profile = evaluate_contract_profiles(task["contract"], specs, checks["results"], stage_id=stage_id)
                verdict = (self.verify(task["task_id"], stage_id) if profile["status"] == "PASS"
                           else GateService(self.store).gate(task["task_id"], stage_id=stage_id))
                if verdict["status"] != "APPROVED":
                    reasons = list(dict.fromkeys(verdict.get("blocked_reasons", []) + verdict.get("rejected_reasons", [])))
                    return (["Prerequisite " + stage_id + " requires current verification: " + reason
                             for reason in (reasons or ["Current independent approval is unavailable"])], None)
                observed = {"task_id": task["task_id"], "stage_id": stage["id"], "workspace_id": checks["workspace_id"],
                    "contract_hash": checks["contract_hash"], "proposal_hash": task["proposal_hash"],
                    "plan_hash": checks["plan_hash"], "fingerprint": checks["fingerprint"]}
                if checks.get("source_changed_during_checks") or verdict.get("fingerprint") != observed["fingerprint"]:
                    raise ValueError("Prerequisite source changed during current verification")
                if binding is not None and binding != observed:
                    raise ValueError("Prerequisite source changed between dependency observations")
                binding = observed
            except (ValueError, OSError, KeyError, TypeError) as ex:
                from .core import redact
                return (["Prerequisite " + stage_id + " requires current verification: " + redact(ex)], None)
        return [], binding

    def _require_prerequisite_binding(self, task_id, stage_id, binding):
        """Compare verified local inputs only at the final mutation boundary."""
        if binding is None:
            return
        task = self.tasks.snapshot(task_id)
        current = {"task_id": task_id, "stage_id": stage_id, "workspace_id": self.store.workspace_id,
            "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"],
            "plan_hash": self.tasks.execution_plan(task_id)["plan_hash"],
            "fingerprint": source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"]}
        if binding != current:
            raise ValueError("Prerequisite evidence changed before dependent mutation; reassess current prerequisites")

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
        stage_blockers = {}
        stage_results = {}
        for stage in ordered:
            stage_id = stage["id"]
            try:
                self._available(task_id, 1)
                current = self.tasks.snapshot(task_id)
                if current["state"] == "CANCELLED" or self.tasks.snapshot(self._root_task(task_id))["state"] == "CANCELLED":
                    raise ValueError("Task is cancelled")
                if current["contract_hash"] != task["contract_hash"] or current["proposal_hash"] != task["proposal_hash"]:
                    raise ValueError("Approved task context changed; resume against current authority")
            except ValueError as ex:
                return {"status": "BLOCKED", "blocked_reasons": [str(ex)], "stage_blockers": stage_blockers, "stage_results": stage_results}
            done = read_record(self.store.db, "stage", task_id + ":" + stage_id)["payload"] or {}
            if done.get("state") == "COMPLETED" and done.get("contract_hash") == task["contract_hash"] and done.get("proposal_hash") == task["proposal_hash"]:
                continue
            try:
                self.tasks.require_approval(task_id, stage_id)
            except ValueError as ex:
                stage_blockers[stage_id] = [str(ex)]
                continue
            dependencies = {dep: read_record(self.store.db, "stage", task_id + ":" + dep)["payload"] or {} for dep in stage["depends_on"]}
            missing_dependencies = [dep for dep, proof in dependencies.items() if proof.get("state") != "COMPLETED"
                or proof.get("contract_hash") != task["contract_hash"] or proof.get("proposal_hash") != task["proposal_hash"]]
            if missing_dependencies:
                stage_blockers[stage_id] = ["Uncompleted stage dependencies: " + ", ".join(missing_dependencies)]
                continue
            child_blockers = GateService(self.store).required_child_blockers(task_id, stage_id)
            if child_blockers:
                stage_blockers[stage_id] = child_blockers
                continue
            try:
                verdict = self._run_stage(task, stage, runner, adapter, resume=resume)
            except (ValueError, OSError, KeyError, TypeError) as ex:
                from .core import redact
                verdict = {"status": "BLOCKED", "blocked_reasons": [redact(ex)]}
            if verdict is not None:
                stage_results[stage_id] = verdict
                stage_blockers[stage_id] = list(dict.fromkeys(verdict.get("blocked_reasons", []) + verdict.get("rejected_reasons", []))) or ["Stage did not complete"]
            else:
                stage_results[stage_id] = (read_record(self.store.db, "stage", task_id + ":" + stage_id)["payload"] or {}).get("gate", {"status": "APPROVED"})
        incomplete = [s["id"] for s in ordered if (read_record(self.store.db, "stage", task_id + ":" + s["id"])["payload"] or {}).get("state") != "COMPLETED"]
        if incomplete:
            reasons = list(dict.fromkeys(reason for blockers in stage_blockers.values() for reason in blockers))
            for stage_id, blockers in stage_blockers.items():
                stage_results.setdefault(stage_id, {"status": "BLOCKED", "blocked_reasons": blockers})
            original = stage_results.get(ordered[0]["id"], {}) if len(ordered) == 1 else {}
            reasons += ["Remaining stage obligations: " + ", ".join(incomplete)]
            return {**original, "status": "BLOCKED", "awaiting_approval": not stage_blockers,
                    "stage_blockers": stage_blockers, "stage_results": stage_results, "reasons": reasons, "blocked_reasons": reasons}
        full_gate = GateService(self.store).gate(task_id)
        if full_gate["status"] == "APPROVED":
            return full_gate
        self.check(task_id)
        return self.verify(task_id)

    def _run_stage(self, task, stage, runner, adapter, *, resume):
        """Settle one stage; the scheduler retains its blockers and runs independent work."""
        task_id, stage_id = task["task_id"], stage["id"]
        while True:
            # Every corrective iteration needs current prerequisite proof before
            # reserving another candidate, including explicit resumes.
            prerequisite_blockers, _ = self._revalidate_prerequisites(task, stage)
            if prerequisite_blockers:
                return {"status": "BLOCKED", "blocked_reasons": prerequisite_blockers}
            progress_key = task_id + ":" + stage_id
            progress = read_record(self.store.db, "cycle", progress_key)["payload"] or {}
            resume_verdict = None
            if resume:
                _, recovery, recovery_groups = self._recovery_context(self.store.db, task_id, stage_id)
                stopped = any(recovery["groups"][name]["stopped"] or recovery["groups"][name].get("diagnostic_inflight")
                    or (recovery["groups"][name]["probe_pending"] and not self._probe_authorized(task_id, stage_id, name, recovery["groups"][name])) for name in recovery_groups)
                inflight = any(recovery["groups"][name].get("probe_inflight") for name in recovery_groups)
                if stopped:
                    # Explicit resume can observe a changed external artifact,
                    # but does not replay actions or reserve another candidate.
                    self.check(task_id, stage_id)
                    progress = {"phase": "VERIFYING", "contract_hash": task["contract_hash"],
                        "proposal_hash": task["proposal_hash"], "read_only_recovery": True, "resumed_progress": True}
                    self._save("cycle", progress_key, progress)
                    current = self.tasks.snapshot(task_id)
                    self.tasks.transition(task_id, "REVIEWING", expected_revision=current["revision"], reason="Read-only independent reassessment on explicit resume")
                    resume_verdict = self.verify(task_id, stage_id)
                    reconciled = self._record_recovery(task_id, stage_id, [], failed=False)
                    if not reconciled["newly_verified"] and resume_verdict["status"] != "APPROVED":
                        return {"status": "BLOCKED", "blocked_reasons": ["Corrective probe remains stopped; fresh read-only checks and independent verification established no new original progress",
                            *resume_verdict.get("blocked_reasons", [])], "previous_gate": resume_verdict}
                elif inflight or (progress.get("phase") == "IMPLEMENTING"
                        and progress.get("contract_hash") == task["contract_hash"]
                        and progress.get("proposal_hash") == task["proposal_hash"]):
                    # Applying several edits and saving the next checkpoint
                    # are not atomic. Assess actual artifacts, never resubmit
                    # the reserved corrective probe after an interruption.
                    progress.update(phase="VERIFYING", contract_hash=task["contract_hash"],
                                    proposal_hash=task["proposal_hash"], read_only_recovery=True)
                    self._save("cycle", progress_key, progress)
            if (progress.get("phase") in {"DIAGNOSING", "NEEDS_APPROVAL"}
                    and progress.get("proposal_hash") == task["proposal_hash"]):
                return {"status": "BLOCKED", "blocked_reasons": ["Diagnostic was interrupted or requires a revised approved proposal"]}
            continuing = (resume and progress.get("phase") in {"CHECKING", "VERIFYING"}
                          and progress.get("contract_hash") == task["contract_hash"]
                          and progress.get("proposal_hash") == task["proposal_hash"])
            if not continuing:
                try:
                    reservation = self.reserve_cycle(task_id, stage_id)
                except ValueError as ex:
                    return {"status": "BLOCKED", "blocked_reasons": [str(ex)], "task_id": task_id}
                current = self.tasks.snapshot(task_id)
                self.tasks.transition(task_id, "EXECUTING", expected_revision=current["revision"], reason="Executing approved stage " + stage_id)
                progress = {"phase": "IMPLEMENTING", "contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"],
                            "candidate_id": reservation["candidate_id"], "corrective_probe": reservation["corrective_probe"]}
                self._save("cycle", progress_key, progress)
                feedback = read_record(self.store.db, "gate", progress_key)["payload"]
                candidate_authority = self._stage_authority(task, stage)
                candidate = self._call(task_id, lambda timeout: runner.run(adapter, self._request(task_id, "implementation", EDIT_SCHEMA,
                    {"instruction": "Propose minimal approved-scope edits. Empty before_sha256 means a new file. Do not change requirements or proof state.",
                     "stage": stage, "previous_feedback": feedback, "diagnostic": read_record(self.store.db, "diagnostic", task_id)["payload"]}), timeout=timeout, cancel_requested=lambda: self._stop_requested(task_id)))
                self._save("implementation", task_id, candidate["receipt"])
                # Preserve unfinished independent stages before a later stage
                # replaces the task-wide latest candidate. This is a proposal
                # receipt, not a checkpoint asserting that edits were applied.
                self._save("stage_implementation", task_id + ":" + stage_id, candidate["receipt"])
                if candidate["result"]["status"] != "COMPLETE":
                    return {"status": "BLOCKED", "blocked_reasons": [candidate["result"]["blocker"]]}
                if not read_record(self.store.db, "baseline_implementation", task_id)["payload"]:
                    self._save("baseline_implementation", task_id, candidate["receipt"])
                # Candidate generation may be long. Renew the actual prerequisite
                # proof after it returns, before considering any candidate writes.
                prerequisite_blockers, prerequisite_binding = self._revalidate_prerequisites(task, stage)
                if prerequisite_blockers:
                    return {"status": "BLOCKED", "blocked_reasons": prerequisite_blockers}
                # File writes share the same linearized authority boundary as
                # process creation. A replacement approval cannot authorize a
                # candidate generated under the previous scope or ancestry.
                with transaction(self.store.db):
                    if self._stage_authority(task, stage) != candidate_authority:
                        raise ValueError("Candidate authority changed before file mutation")
                    self._available(task_id, 1)
                    self._require_prerequisite_binding(task_id, stage_id, prerequisite_binding)
                    apply_edits(self.store.repo, stage["scope"], candidate["result"]["payload"]["edits"])
                progress.update(phase="CHECKING", fingerprint=source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"])
                self._save("cycle", progress_key, progress)
            if progress["phase"] == "CHECKING" and not progress.get("read_only_recovery"):
                # Approved overlapping edits may regress a prerequisite. Assess
                # their actual result before any dependent action is launched.
                prerequisite_blockers, _ = self._revalidate_prerequisites(task, stage)
                if prerequisite_blockers:
                    return {"status": "BLOCKED", "blocked_reasons": prerequisite_blockers}
                self.execute_actions(task_id, stage)
            if resume_verdict is None:
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
            else:
                # Rechecking here would invalidate the just-captured AC and
                # review bindings; use this same read-only observation cycle.
                verdict = resume_verdict
            if verdict["status"] == "APPROVED":
                self._record_recovery(task_id, stage_id, [], failed=False)
                self._capture_stage(task_id, stage_id, verdict)
                from .journal import Journal
                Journal(self.store).append({"kind": "stage_result", "task_id": task_id, "summary": "Verified stage " + stage_id, "status": "COMPLETED", "evidence_refs": ["stage:" + stage_id]}, event_key=task_id + ":" + stage_id + ":" + task["proposal_hash"])
                break
            if verdict["blocked_reasons"]:
                return verdict
            progress.update(phase="REJECTED")
            self._save("cycle", progress_key, progress)
            resume = False
            failure = self._record_recovery(task_id, stage_id, verdict["rejected_reasons"], failed=not progress.get("resumed_progress", False))
            if failure["stopped"] and task["contract"]["execution_policy"].get("recovery_mode", "fixed") == "progress":
                return {"status": "BLOCKED", "blocked_reasons": ["Corrective probe made no verified progress; automatic mutation is stopped"], "previous_gate": verdict}
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
                    {"instruction": "Find the root cause from actual proof and prior feedback; do not propose new authority.", "failure": verdict, "stage_id": stage_id}),
                    timeout=timeout, cancel_requested=lambda: self._stop_requested(task_id)), task["contract"]["execution_policy"]["diagnostic_timeout_seconds"])
                self._save("diagnostic", task_id, diagnosis)
                if not self._complete_diagnostic(task_id, stage_id, diagnosis):
                    progress.update(phase="NEEDS_APPROVAL")
                    self._save("cycle", progress_key, progress)
                    return {"status": "BLOCKED", "blocked_reasons": ["Diagnostic requires a revised approved proposal"]}
                progress.update(phase="REJECTED")
                self._save("cycle", progress_key, progress)

"""Actual approved check execution; test design and acceptance are separate."""
import hashlib
import uuid
from ..gates import source_snapshot, stage_checks
from ..profiles import validate_evidence_spec
from ..projects import _safe_state
from ..native_paths import state_io
from ..state import transaction, read_record, hash_value
from ..store import now

from ..execution.ports import CheckContext


class CheckService:
    def __init__(self, context: CheckContext, *, target):
        self.context = context
        self.target = target

    def check(self, task_id, stage_id=None):
        context = self.context
        from ..core import execute
        with transaction(context.store.db):
            task = context.tasks.snapshot(task_id)
            authority = context._authority_binding(task_id)
        allowed = []
        for stage in task["contract"]["stages"]:
            if stage_id is not None and stage["id"] != stage_id:
                continue
            try:
                context.tasks.require_approval(task_id, stage["id"], allow_terminal=task["state"] == "COMPLETED")
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
            from ..operations import OperationLedger
            ledger = OperationLedger(context.store)
            for stage in task["contract"]["stages"]:
                if stage["id"] not in allowed:
                    continue
                for action in stage.get("actions", []):
                    if "access" not in action:
                        continue
                    action_id = hash_value({"task": task_id, "stage": stage["id"], "action": action["id"]})
                    intent = {"project_id": context.store.project_id, "workspace_id": context.store.workspace_id, "action": action, "effect_scope": stage["scope"]}
                    operation = ledger.lookup(task_id, stage["id"], action_id, intent)
                    if operation:
                        action_id = operation["action_id"]
                        folder = context.store.root / "tasks" / task_id / "actions" / action_id
                        _safe_state(folder)
                        state_io(folder).mkdir(parents=True, exist_ok=True)
                        ledger.reconcile(action_id, context._observe_action(task_id, stage, action, folder,
                            allow_terminal=True, authority_binding=authority))
        plan = context.tasks.execution_plan(task_id)
        before = source_snapshot(context.store, task["contract"], task_id=task_id)
        folder = context.store.root / "tasks" / task_id / "checks" / uuid.uuid4().hex
        _safe_state(folder)
        state_io(folder).mkdir(parents=True)
        results = []
        check_stages = {}
        for approved_stage in allowed:
            stage = next(s for s in task["contract"]["stages"] if s["id"] == approved_stage)
            for raw in stage_checks(task["contract"], plan["checks"], approved_stage):
                if context._stage_permits_command(stage, raw):
                    check_stages.setdefault(raw["id"], approved_stage)
                elif stage_id is not None:
                    raise ValueError("Check access or scope exceeds the explicitly selected approved stage")
        for raw in plan["checks"]:
            if raw["id"] not in check_stages:
                continue
            actual_stage = check_stages[raw["id"]]
            context.tasks.require_approval(task_id, actual_stage, allow_terminal=task["state"] == "COMPLETED")
            spec = validate_evidence_spec(raw, task["contract"]["profile"])
            if spec["kind"] == "artifact":
                path = self.target(context.store.repo.resolve(), spec["path"])
                result = {"id": spec["id"], "kind": "artifact", "path": str(path),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
            elif spec["kind"] == "manual":
                result = read_record(context.store.db, "manual_evidence", task_id + ":" + spec["id"])["payload"] or {"id": spec["id"], "kind": "manual", "confirmed": False}
            else:
                result = context._call(task_id, lambda timeout: context._execute_command(task_id, spec, spec["argv"],
                    folder / (spec["id"] + ".log"), timeout, actual_stage, allow_terminal=task["state"] == "COMPLETED",
                    authority_binding=authority), spec.get("timeout", 300))
                result.update(id=spec["id"], kind=spec["kind"], parser=spec.get("parser"), configured_argv=spec["argv"])
            results.append(result)
        after = source_snapshot(context.store, task["contract"], task_id=task_id)
        bundle = {"kind": "checks", "task_id": task_id, "fingerprint": before["fingerprint"], "workspace_id": context.store.workspace_id,
                  "contract_hash": task["contract_hash"], "plan_hash": plan["plan_hash"], "results": results,
                  "source_changed_during_checks": before["fingerprint"] != after["fingerprint"], "timestamp": now()}
        return context._save("checks", task_id, bundle)

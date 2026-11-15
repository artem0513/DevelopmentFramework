"""Independent AC and review orchestration; no implementation or self-approval."""
from ..adapters import get_adapter
from ..gates import GateService

from ..execution.ports import VerificationContext


class VerificationService:
    def __init__(self, context: VerificationContext, ac_schema, review_schema):
        self.context = context
        self.ac_schema = ac_schema
        self.review_schema = review_schema

    def verify(self, task_id, stage_id=None):
        context = self.context
        task = context.tasks.snapshot(task_id)
        approved = task["approved_stage_ids"] or ([task["contract"]["stages"][0]["id"]] if task["contract"].get("parent_task_id") else [])
        if not approved:
            raise ValueError("Independent verification requires current proposal approval")
        context.tasks.require_approval(task_id, stage_id or approved[0], allow_terminal=task["state"] == "COMPLETED")
        runner = context._runner(task_id)
        adapter = get_adapter(task["contract"]["execution_policy"]["backend"])
        for role, schema in (("ac", self.ac_schema), ("review", self.review_schema)):
            instruction = ("Independently assess the configured criteria against the original request, actual packet source, source_changes baseline manifest, captured before-files and command logs. Return exactly the configured criterion IDs and preserve expected wording. Evaluate original-request constraints under the relevant criteria; a missing material constraint is UNVERIFIED/BLOCKED, never an extra ignored criterion. PASS needs meaningful evidence. Broader limitations do not invalidate concrete evidenced behavior."
                           if role == "ac" else "Independently review actual source for actionable defects P0-P3. P0/P1/P2 block. Do not downgrade to obtain approval. State coverage limits in summary.")
            result = context._call(task_id, lambda timeout: runner.run(adapter, context._request(task_id, role, schema, {"instruction": instruction, "stage_id": stage_id}), timeout=timeout, cancel_requested=lambda: context._stop_requested(task_id)))
            context._save("verification", task_id + ":" + role, result["receipt"])
        return GateService(context.store).gate(task_id, stage_id=stage_id)

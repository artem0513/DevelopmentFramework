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
        from ..testing.adequacy import TestAdequacy, required, ADEQUACY_SCHEMA
        adequacy = required(task['contract'], stage_id)
        test_plan = TestAdequacy(context.store).snapshot(task_id)['payload'] if adequacy else None
        if adequacy and not test_plan:
            raise ValueError('Save the original scenario/test adequacy plan before independent verification')
        if adequacy and (test_plan['contract_hash'] != task['contract_hash']
                or test_plan['proposal_hash'] != task['proposal_hash']
                or test_plan['execution_plan_hash'] != context.tasks.execution_plan(task_id)['plan_hash']):
            raise ValueError('Refresh the stale test adequacy plan before independent verification')
        approved = task["approved_stage_ids"] or ([task["contract"]["stages"][0]["id"]] if task["contract"].get("parent_task_id") else [])
        if not approved:
            raise ValueError("Independent verification requires current proposal approval")
        context.tasks.require_approval(task_id, stage_id or approved[0], allow_terminal=task["state"] == "COMPLETED")
        runner = context._runner(task_id)
        adapter = get_adapter(task["contract"]["execution_policy"]["backend"])
        sessions = [('adequacy', 'review', ADEQUACY_SCHEMA)] if adequacy else []
        sessions.extend((("ac", "ac", self.ac_schema), ("review", "review", self.review_schema)))
        for label, role, schema in sessions:
            instruction = ("Independently assess the configured criteria against the original request, actual packet source, source_changes baseline manifest, captured before-files and command logs. Return exactly the configured criterion IDs and preserve expected wording. Evaluate original-request constraints under the relevant criteria; a missing material constraint is UNVERIFIED/BLOCKED, never an extra ignored criterion. PASS needs meaningful evidence. Broader limitations do not invalidate concrete evidenced behavior."
                           if role == "ac" else "Independently review actual source for actionable defects P0-P3. P0/P1/P2 block. Do not downgrade to obtain approval. State coverage limits in summary.")
            handoff = {'instruction': instruction, 'stage_id': stage_id}
            if label == 'adequacy':
                handoff.update(purpose='test_adequacy', test_plan_sha256=test_plan['test_plan_sha256'],
                    instruction='Independently assess test adequacy against original requirements, scenario plan, actual test sources/assertions and executed logs. Challenge omitted material risks and consumer boundaries. Positive counts alone do not prove adequacy. Return only the configured adequacy schema; missing required proof is UNVERIFIED/BLOCKED.')
            result = context._call(task_id, lambda timeout: runner.run(adapter, context._request(task_id, role, schema, handoff), timeout=timeout, cancel_requested=lambda: context._stop_requested(task_id)))
            key = task_id + ':' + label + (':' + stage_id if label == 'adequacy' and stage_id else '')
            context._save("verification", key, result["receipt"])
        return GateService(context.store).gate(task_id, stage_id=stage_id)

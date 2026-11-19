"""Independent AC and review orchestration; no implementation or self-approval."""
import copy
from ..adapters import get_adapter
from ..gates import GateService

from ..execution.ports import VerificationContext


def _scoped_ac_schema(base, acceptance_ids, *, enabled):
    """Constrain opt-in bugfix replies without changing legacy wire contracts."""
    if not enabled:
        return base
    schema = copy.deepcopy(base)
    criteria = schema['properties']['criteria']
    criteria.update(minItems=len(acceptance_ids), maxItems=len(acceptance_ids))
    if acceptance_ids:
        criteria['items']['properties']['id'] = {
            **criteria['items']['properties']['id'], 'enum': acceptance_ids}
    return schema


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
        from ..testing.bugfix import assessment_schema, required as bugfix_required
        from ..testing.adequacy import _compatible_stages
        from ..profiles import stage_evidence_specs
        checks=context.tasks.execution_plan(task_id)['checks']
        selected=[stage for stage in task['contract']['stages'] if stage_id is None or stage['id']==stage_id]
        active_ac={name for stage in selected for name in stage['acceptance_ids']}
        verification_scope={'stage_id':stage_id,
            'acceptance_ids':[row['id'] for row in task['contract']['acceptance'] if stage_id is None or row['id'] in active_ac],
            'check_ids':[row['id'] for row in stage_evidence_specs(task['contract'],checks,stage_id)],
            'scenario_ids':[row['id'] for row in (test_plan or {}).get('scenarios',[])
                if stage_id is None or stage_id in _compatible_stages(task['contract'],checks,row)],
            'bugfix_stage_ids':[stage['id'] for stage in selected if bugfix_required(task['contract'],stage['id'])]}
        active_scenarios={row['id']:row for row in (test_plan or {}).get('scenarios',[])}
        verification_scope['scenario_check_ids']={name:active_scenarios[name]['evidence_ids'] for name in verification_scope['scenario_ids']}
        verification_scope['bugfix_check_ids']={row['stage_id']:sorted({check for name in row['regression_scenario_ids'] for check in active_scenarios[name]['evidence_ids']})
            for row in (test_plan or {}).get('bugfixes',[]) if row['stage_id'] in verification_scope['bugfix_stage_ids']}
        ac_schema=_scoped_ac_schema(self.ac_schema,verification_scope['acceptance_ids'],
            enabled=bugfix_required(task['contract']))
        sessions = [('adequacy', 'review', assessment_schema(ADEQUACY_SCHEMA,task['contract'],stage_id,plan=test_plan))] if adequacy else []
        sessions.extend((("ac", "ac", ac_schema), ("review", "review", self.review_schema)))
        for label, role, schema in sessions:
            instruction = ("Independently assess the configured criteria against the original request, actual packet source, source_changes baseline manifest, captured before-files and command logs. Return exactly the configured criterion IDs and preserve expected wording. Evaluate original-request constraints under the relevant criteria; a missing material constraint is UNVERIFIED/BLOCKED, never an extra ignored criterion. PASS needs meaningful evidence. Broader limitations do not invalidate concrete evidenced behavior."
                           if role == "ac" else "Independently review actual source for actionable defects P0-P3. P0/P1/P2 block. Do not downgrade to obtain approval. State coverage limits in summary.")
            handoff = {'instruction': instruction, 'stage_id': stage_id,'verification_scope':verification_scope}
            if label == 'ac' and stage_id is None:
                from ..lead_review import LeadReview, acceptance_schema
                if LeadReview(context.store).configured(task_id):
                    schema = acceptance_schema(schema)
                    handoff['instruction'] += (' Explicitly challenge the original lead classification, its complete requirement-anchored critiques and evidence/resolutions. '
                        'Return the extra lead_review disposition. A structurally eligible but semantically complex exemption is DISAGREE; '
                        'three generic PASS summaries are INSUFFICIENT. A justified current trivial exemption may have sufficient exemption evidence without three passes. '
                        'Absent material proof is UNVERIFIED. Do not add uncontracted criterion IDs.')
            if label == 'adequacy':
                handoff.update(purpose='test_adequacy', test_plan_sha256=test_plan['test_plan_sha256'],
                    instruction='Independently assess test adequacy against original requirements, scenario plan, actual test sources/assertions and executed logs. Challenge omitted material risks and consumer boundaries. Positive counts alone do not prove adequacy. Return only the configured adequacy schema; missing required proof is UNVERIFIED/BLOCKED.')
            if bugfix_required(task['contract'],stage_id):
                handoff['instruction'] += (' Independently challenge the bugfix investigation: trace the real caller, boundary, callee and authoritative contract to the first divergence; distinguish a confirmed cause from a symptom or workaround. '
                    'Establish why this correction is necessary, including alternatives to any new merger/wrapper/cache/retry and preservation of other legitimate response fields. '
                    'Inspect a discriminating original-failure regression through the actual consumer, grounded fixtures and before/after or supported fault-injection evidence. Helper-only green tests are insufficient. '
                    'For adequacy return separate cause, necessity and regression judgments for each active software stage. Missing proof is UNVERIFIED/BLOCKED; confirmed inadequacy is FAIL. Do not invent missing facts or require destructive live reproduction.')
            handoff['instruction'] += (' The host-computed verification_scope names the obligations CURRENT at this gate. '
                'AC criteria IDs come ONLY from acceptance_ids; never put scenario IDs or stage IDs in criteria. Adequacy scenario IDs come ONLY from scenario_ids, and its bugfix stage IDs ONLY from bugfix_stage_ids. '
                'Use scenario_check_ids and bugfix_check_ids for the corresponding evidence.check_id values; an unrelated current check is not mapped proof. '
                'For a stage these sets may be empty; do not require a downstream check or a causal judgment outside this stage schema as current missing_coverage. '
                'Keep later/unselected obligations explicitly pending in limitations or summary, never claim they passed. At a whole-task gate all configured mandatory obligations remain current. '
                'Missing CURRENT evidence still blocks; substantive defects observable in current source remain actionable findings regardless of the later execution schedule.')
            result = context._call(task_id, lambda timeout: runner.run(adapter, context._request(task_id, role, schema, handoff), timeout=timeout, cancel_requested=lambda: context._stop_requested(task_id)))
            key = task_id + ':' + label + (':' + stage_id if label == 'adequacy' and stage_id else '')
            context._save("verification", key, result["receipt"])
        return GateService(context.store).gate(task_id, stage_id=stage_id)

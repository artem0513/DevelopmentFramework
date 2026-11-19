"""Bugfix-specific evidence contracts; semantic judgment belongs to reviewers."""
import copy
from pathlib import PurePosixPath, PureWindowsPath

from ..profiles import stage_profile
from ..state import identifier, text


POLICY={'version':1,'required':True}
FIELDS={'stage_id','symptom','expected','first_divergence','cause_status','trace',
        'causal_evidence','fix_rationale','alternatives','regression_scenario_ids'}


def required(contract,stage_id=None):
    stages=contract.get('stages',[])
    selected=[s for s in stages if s['id']==stage_id] if stage_id is not None else stages
    if stage_id is not None and not selected:
        raise ValueError('Unknown bugfix evidence stage')
    if 'bugfix_evidence' not in contract:
        return False
    policy=contract['bugfix_evidence']
    if (not isinstance(policy,dict) or set(policy)!=set(POLICY)
            or type(policy.get('version')) is not int or policy['version']!=1
            or policy.get('required') is not True):
        raise ValueError('Invalid bugfix_evidence policy')
    # Coordination/delivery phases with no acceptance obligations cannot map a
    # required regression; they are not a distinct causal repair boundary.
    return any(stage_profile(contract,s['id'])=='software' and s.get('acceptance_ids') for s in selected)


def _source_path(path):
    text(path,'bugfix trace path',limit=2000)
    if ('\\' in path or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()
            or '..' in PurePosixPath(path).parts or path.startswith('_ase_')
            or PurePosixPath(path).as_posix()!=path or path=='.'):
        raise ValueError('Bugfix trace requires a captured relative source path')


def validate_dossiers(payload,contract=None,checks=None):
    rows=payload.get('bugfixes',[])
    if not isinstance(rows,list) or len(rows)>100:
        raise ValueError('Bugfix investigations require a bounded list')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!=FIELDS:
            raise ValueError('Invalid bugfix investigation fields')
        stage=identifier(row['stage_id'],'bugfix stage')
        if stage in seen:
            raise ValueError('Duplicate bugfix stage')
        seen.add(stage)
        for field in ('symptom','expected','first_divergence','causal_evidence','fix_rationale','alternatives'):
            text(row[field],'bugfix '+field,limit=8000)
        if row['cause_status'] not in ('CONFIRMED','HYPOTHESIS','WORKAROUND'):
            raise ValueError('Invalid bugfix cause status')
        trace=row['trace']
        if not isinstance(trace,list) or not 1<=len(trace)<=30:
            raise ValueError('Bugfix requires a bounded caller/contract trace')
        for ref in trace:
            if not isinstance(ref,dict) or set(ref) not in ({'path','locator','observation'},{'path','locator','observation','version'}):
                raise ValueError('Invalid bugfix source trace')
            if ref.get('version','current') not in ('current','before'):
                raise ValueError('Bugfix trace version must be current or before')
            _source_path(ref['path'])
            for field in ('locator','observation'):
                text(ref[field],'bugfix '+field,limit=8000)
        ids=row['regression_scenario_ids']
        if (not isinstance(ids,list) or not 1<=len(ids)<=100
                or any(not isinstance(value,str) for value in ids) or len(ids)!=len(set(ids))):
            raise ValueError('Bugfix requires unique regression scenarios')
        for value in ids:identifier(value,'bugfix scenario')
    if contract is not None:
        enabled=required(contract)
        expected={s['id'] for s in contract['stages'] if enabled and required(contract,s['id'])}
        if seen!=expected:
            raise ValueError('Bugfix investigations must cover exactly the configured software stages')
        scenarios={s['id']:s for s in payload['scenarios']}
        from .adequacy import _compatible_stages
        for row in rows:
            for name in row['regression_scenario_ids']:
                scenario=scenarios.get(name)
                if (scenario is None or not scenario['required']
                        or row['stage_id'] not in _compatible_stages(contract,checks,scenario)):
                    raise ValueError('Bugfix regression needs a required compatible scenario: '+name)
    return rows


def assessment_schema(base,contract,stage_id=None,*,plan=None):
    if not required(contract,stage_id):
        return base
    from .adequacy import _object, _ASSESSMENT, _STRING
    result=copy.deepcopy(base)
    result['properties']['bugfixes']={'type':'array','maxItems':100,'items':_object({
        'stage_id':_STRING,**{field:_object(copy.deepcopy(_ASSESSMENT)) for field in ('cause','necessity','regression')}})}
    active=[stage['id'] for stage in contract['stages'] if (stage_id is None or stage['id']==stage_id) and required(contract,stage['id'])]
    row=result['properties']['bugfixes']['items']
    row['properties']['stage_id']={**_STRING,'enum':active}
    if plan is not None:
        scenarios={item['id']:item for item in plan['scenarios']}
        checks=sorted({check for dossier in plan['bugfixes'] if dossier['stage_id'] in active
            for name in dossier['regression_scenario_ids'] for check in scenarios[name]['evidence_ids']})
        if not checks:raise ValueError('Bugfix schema needs mapped regression check IDs')
        for field in ('cause','necessity','regression'):
            row['properties'][field]['properties']['evidence']['items']['properties']['check_id']={**_STRING,'enum':checks}
    result['required'].append('bugfixes')
    return result


def repair_targets(contract,stage_id):
    """Map an editing phase to its nearest related causal acceptance boundary."""
    if not required(contract):
        return set()
    by_id={row['id']:row for row in contract['stages']}
    if stage_id not in by_id:
        raise ValueError('Unknown bugfix repair stage')
    stage=by_id[stage_id]
    if required(contract,stage_id):
        return {stage_id}
    scope=stage.get('scope',contract.get('scope',{}))
    if (stage_profile(contract,stage_id)!='software'
            or ('file_write' not in scope.get('effects',[]) and not stage.get('actions'))):
        return set()
    def boundaries(forward):
        seen={stage_id};pending=[stage_id];found=set()
        while pending:
            current=pending.pop()
            neighbors=([row['id'] for row in by_id.values() if current in row.get('depends_on',[])]
                       if forward else by_id[current].get('depends_on',[]))
            for name in neighbors:
                if name in seen:continue
                seen.add(name)
                if required(contract,name):found.add(name)
                else:pending.append(name)
        return found
    targets=boundaries(True) or boundaries(False)
    if not targets:
        raise ValueError('Mutating bugfix stage needs a related causal acceptance boundary; refine its technical stage mapping')
    return targets


def repair_readiness(store,task_id,stage_id):
    """Before a fresh causal-repair candidate; never a recovery/diagnostic fence."""
    from .adequacy import TestAdequacy, _validate_plan
    from ..tasks import TaskService
    tasks=TaskService(store);task=tasks.snapshot(task_id)
    if not required(task['contract']):
        return {'ready':True}
    reason=None
    try:
        targets=repair_targets(task['contract'],stage_id)
        if not targets:
            return {'ready':True}
        plan=TestAdequacy(store).snapshot(task_id)['payload']
        if not plan:
            reason='Save the bugfix investigation before proposing a causal repair'
        else:
            execution=tasks.execution_plan(task_id)
            expected={'contract_hash':task['contract_hash'],'proposal_hash':task['proposal_hash'],
                'execution_plan_hash':execution['plan_hash']}
            if any(plan[key]!=value for key,value in expected.items()):
                reason='Refresh the stale bugfix investigation before proposing a causal repair'
            else:
                _validate_plan(task['contract'],execution['checks'],{key:plan[key] for key in ('scenarios','not_applicable','bugfixes') if key in plan})
                if any(row['cause_status']!='CONFIRMED' for row in plan['bugfixes'] if row['stage_id'] in targets):
                    reason='Investigate the unconfirmed cause before proposing a causal repair'
    except (ValueError,KeyError,TypeError):
        reason='Refine the invalid bugfix investigation or technical stage mapping before proposing a causal repair'
    if reason:
        return {'ready':False,'reason':reason,'needs_owner_input':False,
            'next_action':'Continue authorized investigation with useful safe probes or reversible diagnostic tests; save current causal evidence and rationale. Ask only if a material owner-only gap remains.'}
    return {'ready':True}


def assess(contract,plan,result,stage_id,files,scenarios,specs,profile,assessment,reasons,rejected,*,captured_stage=None):
    if not required(contract,stage_id):
        return
    active={row['stage_id']:row for row in plan['bugfixes'] if stage_id is None or row['stage_id']==stage_id}
    rows=result.get('bugfixes',[])
    ids=[row['stage_id'] for row in rows]
    captured={row['stage_id'] for row in plan['bugfixes'] if captured_stage is None or row['stage_id']==captured_stage}
    if len(ids)!=len(set(ids)) or set(ids)!=captured:
        reasons.append('Independent bugfix judgments must cover exactly the active stages')
        return
    for row in rows:
        if row['stage_id'] not in active:
            continue
        dossier=active[row['stage_id']]
        label='bugfix '+row['stage_id']
        if dossier['cause_status']!='CONFIRMED':
            reasons.append('Bugfix cause is not confirmed: '+row['stage_id'])
        for ref in dossier['trace']:
            # Before-images are supplied only by the host's authenticated source
            # capture. Never search arbitrary files or silently change versions.
            path=('_ase_before/' if ref.get('version','current')=='before' else '')+ref['path']
            source=files.get(path)
            if source is None or ref['locator'] not in source['content']:
                reasons.append('Bugfix caller/contract trace unavailable: '+path+'/'+ref['locator'])
        allowed={check for name in dossier['regression_scenario_ids'] for check in scenarios[name]['evidence_ids']}
        for field in ('cause','necessity','regression'):
            assessment(row[field],label+'/'+field,allowed,specs,profile,files,reasons,rejected)

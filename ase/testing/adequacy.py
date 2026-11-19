"""Independent test-adequacy plans and current-receipt policy.

This service never runs tests, starts a model, or grants task authority. A host
supplies fresh review-role Runner evidence; semantic quality remains a separate
independent judgment rather than a test-count heuristic.
"""
import ast
import copy
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from ..native_paths import state_io
from ..profiles import COMMAND_KINDS, evaluate_contract_profiles, stage_evidence_specs, stage_profile
from ..protocol import decode_json, validate_payload
from ..runner import checked_path, verify_receipt
from ..state import encode, hash_value, identifier, read_record, text, transaction, write_record
from ..tasks import TaskService


def _object(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


_STRING={'type':'string','minLength':1,'maxLength':8000}
_STRINGS={'type':'array','maxItems':100,'items':_STRING}
_STATUS={'type':'string','enum':['PASS','FAIL','UNVERIFIED','BLOCKED']}
_PROOF=_object({'check_id':_STRING,'test_source':_STRING,'test_id':_STRING,'assertion':_STRING})
_PROOFS={'type':'array','maxItems':100,'items':_PROOF}
_ASSESSMENT={'status':_STATUS,'evidence':_PROOFS,'missing_coverage':_STRINGS,'reason':_STRING}
ADEQUACY_SCHEMA=_object({
    'scenarios':{'type':'array','maxItems':100,'items':_object({'id':_STRING,**_ASSESSMENT})},
    'additional_scenarios':{'type':'array','maxItems':100,'items':_object({
        'risk':_STRING,'boundary':_STRING,'expected':_STRING,**_ASSESSMENT})},
    'missing_coverage':_STRINGS,'summary':_STRING})


def required(contract, stage_id=None):
    """Read explicit policy without normalizing or rewriting legacy contracts."""
    stages=contract.get('stages',[])
    selected=[stage for stage in stages if stage['id']==stage_id] if stage_id is not None else stages
    if stage_id is not None and not selected:
        raise ValueError('Unknown test adequacy stage')
    policy=contract.get('test_adequacy')
    if 'test_adequacy' not in contract:
        return False
    if (not isinstance(policy,dict) or set(policy)!={'version','required'}
            or type(policy['version']) is not int or policy['version']!=1
            or type(policy['required']) is not bool):
        raise ValueError('Invalid test_adequacy policy')
    return policy['required'] and any(stage_profile(contract,stage['id'])=='software' for stage in selected)


def _ids(values, name, *, nonempty=True):
    if not isinstance(values,list) or len(values)>100 or (nonempty and not values):
        raise ValueError(name+' requires a bounded list')
    identifiers=[identifier(value,name) for value in values]
    if len(identifiers)!=len(set(identifiers)):
        raise ValueError('Duplicate '+name)
    return set(identifiers)


def _rows(payload):
    if (not isinstance(payload,dict) or not {'scenarios','not_applicable'}.issubset(payload)
            or set(payload)-{'scenarios','not_applicable','bugfixes'}):
        raise ValueError('Test plan requires scenarios and not_applicable')
    if len(encode(payload))>200000:
        raise ValueError('Test plan exceeds bound')
    scenarios,omitted=payload['scenarios'],payload['not_applicable']
    if not isinstance(scenarios,list) or len(scenarios)>100 or not isinstance(omitted,list) or len(omitted)>100:
        raise ValueError('Test plan requires bounded scenario/risk lists')
    seen=set()
    for row in scenarios:
        if not isinstance(row,dict) or set(row)!={'id','acceptance_ids','risk','boundary','expected','evidence_ids','required'}:
            raise ValueError('Invalid test scenario fields')
        name=identifier(row['id'],'scenario id')
        if name in seen:
            raise ValueError('Duplicate test scenario ID')
        seen.add(name)
        _ids(row['acceptance_ids'],'scenario acceptance IDs')
        _ids(row['evidence_ids'],'scenario evidence IDs')
        for field in ('risk','boundary','expected'):
            text(row[field],field,limit=8000)
        if type(row['required']) is not bool:
            raise ValueError('Scenario required must be boolean')
    risks=set()
    for row in omitted:
        if not isinstance(row,dict) or set(row)!={'risk','reason'}:
            raise ValueError('Excluded risk needs risk and reason')
        for field in ('risk','reason'):
            text(row[field],field,limit=8000)
        if row['risk'] in risks:
            raise ValueError('Duplicate excluded risk')
        risks.add(row['risk'])
    from .bugfix import validate_dossiers
    validate_dossiers(payload)
    return scenarios


def _compatible_stages(contract,checks,scenario):
    acceptance=set(scenario['acceptance_ids'])
    evidence=set(scenario['evidence_ids'])
    return [stage['id'] for stage in contract['stages']
        if stage_profile(contract,stage['id'])=='software'
        and acceptance.issubset(stage['acceptance_ids'])
        and evidence.issubset({spec['id'] for spec in stage_evidence_specs(contract,checks,stage['id'])})]


def _validate_plan(contract,checks,payload):
    scenarios=_rows(payload)
    from .bugfix import validate_dossiers
    validate_dossiers(payload,contract,checks)
    known_ac={ac['id'] for ac in contract['acceptance']}
    known_checks={spec['id'] for spec in checks}
    mandatory={(stage['id'],ac['id']) for ac in contract['acceptance'] if ac.get('mandatory',True)
        for stage in contract['stages'] if stage_profile(contract,stage['id'])=='software'
        and ac['id'] in stage['acceptance_ids']}
    covered=set()
    for row in scenarios:
        if (not set(row['acceptance_ids']).issubset(known_ac)
                or not set(row['evidence_ids']).issubset(known_checks)
                or not _compatible_stages(contract,checks,row)):
            raise ValueError('Test scenario has unknown or incompatible acceptance/evidence/stage mapping: '+row['id'])
        if row['required']:
            covered.update((stage,ac) for stage in _compatible_stages(contract,checks,row) for ac in row['acceptance_ids'])
    if mandatory-covered:
        raise ValueError('Mandatory software acceptance lacks required scenarios: '+', '.join(stage+'/'+ac for stage,ac in sorted(mandatory-covered)))
    if not scenarios:
        raise ValueError('Configured software test plan requires scenarios')


def _outcome(status,reasons=(),*,configured=True,rejected=()):
    return {'configured':configured,'status':status,'blocked_reasons':list(dict.fromkeys(reasons)),
        'rejected_reasons':list(dict.fromkeys(rejected))}


def _source_cases(content):
    """Python definitions, not matching text in comments or another class."""
    cases=set()
    def visit(nodes,prefix=''):
        for node in nodes:
            if isinstance(node,ast.ClassDef):
                visit(node.body,prefix+node.name+'.')
            elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                cases.add(prefix+node.name)
    try:
        visit(ast.parse(content).body)
    except (SyntaxError,ValueError):
        pass
    return cases


def _script_argument(argv):
    """Return Python's direct script argument, never an arbitrary argv mention."""
    if not argv or not re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?',Path(argv[0]).name,re.I):
        return None
    index=1
    while index<len(argv):
        argument=argv[index]
        if argument in ('-m','-c'):
            return None
        if argument=='--':
            return argv[index+1] if index+1<len(argv) else None
        if argument in ('-W','-X'):
            index+=2
        elif argument.startswith('-'):
            index+=1
        else:
            return argument
    return None


def _unittest_module(path,repo,observation):
    """Bind unittest's module name to the source and the actual invocation root."""
    source=(repo/path).resolve()
    cwd=Path(observation['cwd']).resolve()
    argv=observation['argv']
    script=_script_argument(argv)
    if script is not None:
        return '__main__' if (cwd/script).resolve()==source else None
    top=cwd
    if 'discover' in argv:
        arguments=argv[argv.index('discover')+1:]
        start='.'; explicit_top=None; positional=[]; index=0
        while index<len(arguments):
            argument=arguments[index]
            if argument in ('-s','--start-directory','-t','--top-level-directory','-p','--pattern','-k'):
                if index+1==len(arguments):
                    return None
                if argument in ('-s','--start-directory'):start=arguments[index+1]
                elif argument in ('-t','--top-level-directory'):explicit_top=arguments[index+1]
                index+=2
            elif argument.startswith('--start-directory='):
                start=argument.split('=',1)[1]; index+=1
            elif argument.startswith('--top-level-directory='):
                explicit_top=argument.split('=',1)[1]; index+=1
            else:
                if not argument.startswith('-'):positional.append(argument)
                index+=1
        if positional:start=positional[0]
        if explicit_top is None and len(positional)>2:explicit_top=positional[2]
        top=(cwd/(explicit_top if explicit_top is not None else start)).resolve()
    try:
        parts=list(source.relative_to(top).with_suffix('').parts)
    except ValueError:
        return None
    if parts and parts[-1]=='__init__':parts.pop()
    module='.'.join(parts) if parts and all(part.isidentifier() for part in parts) else None
    return None if module=='__main__' else module


def _unittest_cases(output):
    """Known verbose unittest identities/outcomes, including failure subtests."""
    pending=None
    for line in output.splitlines():
        if not line.strip():continue
        failure=re.match(r'^\s*(?:FAIL|ERROR): (\w+) \(([\w.]+)\)',line)
        match=re.match(r'^\s*(\w+) \(([\w.]+)\)(?: \([^\n]*\))?(?: \.\.\. (.*))?$',line)
        if failure:
            method,identity=failure.groups(); status='FAIL'
        elif match:
            method,identity,status=match.groups()
            if status is None:
                pending=(method,identity); continue
        elif pending and (description:=re.match(r'^.* \.\.\. (.*)$',line)):
            method,identity=pending; status=description[1]
        else:
            pending=None; continue
        pending=None
        # Python versions print either module.Class or module.Class.method.
        qualified=identity if identity.endswith('.'+method) else identity+'.'+method
        if status in ('FAIL','ERROR','unexpected success'):
            outcome='FAIL'
        elif status=='ok':
            outcome='PASS'
        else:
            outcome='BLOCKED'
        yield qualified,outcome


def _case_execution(proof,spec,observations,content,repo):
    """Mechanically bind a cited case to a current log, not semantic adequacy.

    Only verbose unittest and pytest node outcomes are understood. Other runners
    and aggregate-only output remain unproved until a reliable case adapter is
    supplied; successful counts and source locators are never substitutes.
    """
    matches=[row for row in observations if row.get('id')==proof['check_id']]
    if len(matches)!=1:
        return 'BLOCKED'
    observation=matches[0]
    if (observation.get('configured_argv')!=spec['argv'] or not observation.get('started')
            or not observation.get('ended') or not observation.get('cwd')):
        return 'BLOCKED'
    from ..core import validate_command_argv
    try:
        validate_command_argv(observation,spec['argv'])
    except (ValueError,KeyError,TypeError):
        return 'BLOCKED'
    raw=state_io(Path(observation['log'])).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=observation.get('log_sha256'):
        return 'BLOCKED'
    output=raw.decode('utf-8','replace')
    definitions=_source_cases(content)
    parser=spec.get('parser')
    outcomes=[]
    path=proof['test_source']; test_id=proof['test_id']
    # Parameter identities must be cited exactly; they are separate executions.
    base,separator,parameters=test_id.partition('[')
    parameters=separator+parameters
    module=_unittest_module(path,repo,observation) if parser in (None,'unittest') else None
    candidates=[]
    for name in definitions:
        locators={name,path+'::'+name.replace('.','::')}
        if sum(other.rsplit('.',1)[-1]==name.rsplit('.',1)[-1] for other in definitions)==1:
            locators.add(name.rsplit('.',1)[-1])
        if module:locators.add(module+'.'+name)
        if base in locators:candidates.append(name)
    if len(candidates)!=1:
        return 'BLOCKED'
    name=candidates[0]
    if module and not parameters:
        outcomes.extend(status for identity,status in _unittest_cases(output) if identity==module+'.'+name)
    if parser in (None,'pytest'):
        for line in output.splitlines():
            match=re.match(r'^([^\s]+::[^\s]+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?:\s|$)',line)
            if not match:continue
            node,status=match.groups(); source,_,case=node.partition('::')
            if ((Path(observation['cwd'])/source).resolve()!=(repo/path).resolve()
                    or case!='::'.join(name.split('.'))+parameters):
                continue
            outcomes.append('PASS' if status=='PASSED' else 'FAIL' if status in ('FAILED','ERROR','XPASS') else 'BLOCKED')
    if 'FAIL' in outcomes:return 'FAIL'
    return 'PASS' if outcomes and all(status=='PASS' for status in outcomes) else 'BLOCKED'


class TestAdequacy:
    """CAS plan storage and deterministic assessment policy over Runner receipts."""
    def __init__(self,store):
        self.store=store

    def _selected_receipt(self,task_id,stage_id):
        key=task_id+':adequacy'+(':'+stage_id if stage_id else '')
        receipt=read_record(self.store.db,'verification',key)['payload']
        if receipt is None and stage_id is not None:
            key=task_id+':adequacy'
            receipt=read_record(self.store.db,'verification',key)['payload']
        return key,receipt

    def binding(self,task_id,stage_id=None):
        task=TaskService(self.store).snapshot(task_id)
        if not required(task['contract'],stage_id):
            return None
        key,receipt=self._selected_receipt(task_id,stage_id)
        return hash_value({'plan':read_record(self.store.db,'test_adequacy_plan',task_id)['payload'],
            'receipt_key':key,'receipt':receipt})

    def assessment(self,task_id,stage_id=None):
        """Captured assessment data for later roles, never a substitute approval."""
        task=TaskService(self.store).snapshot(task_id)
        if not required(task['contract'],stage_id):
            return {'state':'NOT_CONFIGURED'}
        key,receipt=self._selected_receipt(task_id,stage_id)
        if receipt is None:
            return {'state':'PENDING','receipt_key':key}
        try:
            verified=verify_receipt(receipt)
            return {'state':'CAPTURED','receipt_key':key,'bindings':receipt['bindings'],
                'request_identity':verified['request_identity'],'evidence_kind':verified['evidence_kind'],
                'payload':verified['result']['payload'],
                'authority':'Captured judgment data; original AC/review and current gate remain independent'}
        except (ValueError,OSError,KeyError,TypeError) as error:
            from ..core import redact
            return {'state':'UNAVAILABLE','receipt_key':key,'reason':str(redact(error))[:1000]}

    def snapshot(self,task_id):
        identifier(task_id,'task id')
        row=read_record(self.store.db,'test_adequacy_plan',task_id)
        value=row['payload']
        if value is None:
            return row
        fields={'version','task_id','contract_hash','proposal_hash','execution_plan_hash',
            'scenarios','not_applicable','test_plan_sha256'}
        if (not isinstance(value,dict) or set(value) not in (fields,fields|{'bugfixes'}) or type(value['version']) is not int
                or value['version']!=1 or value['task_id']!=task_id or len(encode(value))>200000):
            raise ValueError('Invalid bounded test plan record')
        for field in ('contract_hash','proposal_hash','execution_plan_hash','test_plan_sha256'):
            if not isinstance(value[field],str) or not re.fullmatch('[0-9a-f]{64}',value[field]):
                raise ValueError('Invalid test plan binding: '+field)
        _rows({key:value[key] for key in ('scenarios','not_applicable','bugfixes') if key in value})
        if hash_value({key:item for key,item in value.items() if key!='test_plan_sha256'})!=value['test_plan_sha256']:
            raise ValueError('Test plan hash differs from captured plan')
        return row

    def plan(self,task_id,payload,*,expected_revision):
        payload=copy.deepcopy(payload)
        tasks=TaskService(self.store)
        with transaction(self.store.db):
            task=tasks.snapshot(task_id)
            if task['legacy_v1'] or not required(task['contract']):
                raise ValueError('Test adequacy plan requires configured software policy')
            if not task.get('proposal_hash'):
                raise ValueError('Test plan requires a current proposal')
            execution=tasks.execution_plan(task_id)
            _validate_plan(task['contract'],execution['checks'],payload)
            value={'version':1,'task_id':task_id,'contract_hash':task['contract_hash'],
                'proposal_hash':task['proposal_hash'],'execution_plan_hash':execution['plan_hash'],**payload}
            value['test_plan_sha256']=hash_value(value)
            write_record(self.store.db,'test_adequacy_plan',task_id,value,expected_revision=expected_revision)
        return self.snapshot(task_id)

    def _source_files(self,receipt):
        # Reopening a path is safe only when the exact bytes subsequently parsed
        # still match the verified request identity. Never trust a second path read.
        path=checked_path(receipt['artifacts']['request']['path'],directory=False)
        if path.parent!=checked_path(receipt['artifact_root'],directory=True):
            raise ValueError('Adequacy request escapes its execution root')
        with path.open('rb') as stream:
            raw=stream.read(8388609)
        if len(raw)>8388608 or hashlib.sha256(raw).hexdigest()!=receipt['request_sha256']:
            raise ValueError('Adequacy request changed after receipt validation')
        request=decode_json(raw)
        return {row['path']:row for row in request['source_packet']['files']}

    def _independent(self,task_id,receipt,verified,current):
        execution=receipt['execution_id']
        session=verified['result'].get('session_id')
        if not isinstance(session,str) or not session:
            raise ValueError('Independent adequacy provider session is missing')
        if execution==current.get('implementation_execution_id'):
            raise ValueError('Adequacy reused implementation execution')
        others=[]
        for role in ('ac','review'):
            others.append(read_record(self.store.db,'verification',task_id+':'+role)['payload'])
        others.append(read_record(self.store.db,'implementation',task_id)['payload'])
        for row in self.store.db.execute("SELECT key,payload FROM runtime_records WHERE kind='stage_implementation'"):
            if row['key'].startswith(task_id+':'):
                others.append(decode_json(row['payload']))
        for other in others:
            if isinstance(other,dict) and (other.get('execution_id')==execution or other.get('session_id')==session):
                raise ValueError('Adequacy reused another role execution or provider session')

    def gate(self,task_id,*,current,stage_id=None):
        """Assess existing evidence. Source/authority gates remain with GateService."""
        try:
            tasks=TaskService(self.store)
            task=tasks.snapshot(task_id)
            contract=task['contract']
            enabled=required(contract,stage_id)
            if not enabled:
                return _outcome('NOT_CONFIGURED' if 'test_adequacy' not in contract else 'NOT_REQUIRED',configured=False)
            return self._gate(task_id,task,current,stage_id)
        except (ValueError,OSError,KeyError,TypeError) as error:
            return _outcome('BLOCKED',['Test adequacy evidence invalid: '+str(error)])

    def _gate(self,task_id,task,current,stage_id):
        contract=task['contract']
        record=self.snapshot(task_id)
        plan=record['payload']
        if not plan:
            return _outcome('BLOCKED',['Independent test adequacy plan missing'])
        execution=TaskService(self.store).execution_plan(task_id)
        expected={'contract_hash':task['contract_hash'],'proposal_hash':task['proposal_hash'],
            'execution_plan_hash':execution['plan_hash']}
        if any(plan[key]!=value for key,value in expected.items()):
            return _outcome('BLOCKED',['Test adequacy plan is stale'])
        if any(current.get(key)!=expected[field] for key,field in
                (('contract_hash','contract_hash'),('proposal_hash','proposal_hash'),('plan_hash','execution_plan_hash'))):
            return _outcome('BLOCKED',['Test adequacy current contract/proposal/execution plan differs'])
        _validate_plan(contract,execution['checks'],{key:plan[key] for key in ('scenarios','not_applicable','bugfixes') if key in plan})
        key=task_id+':adequacy'+(':'+stage_id if stage_id else '')
        receipt=read_record(self.store.db,'verification',key)['payload']
        captured_stage=stage_id
        if receipt is None and stage_id is not None:
            receipt=read_record(self.store.db,'verification',task_id+':adequacy')['payload']
            captured_stage=None
        if not receipt:
            return _outcome('BLOCKED',['Independent test adequacy receipt missing'])
        root=checked_path(receipt['artifact_root'],directory=True)
        if not root.is_relative_to((self.store.root/'tasks'/task_id/'sessions').resolve()):
            return _outcome('BLOCKED',['Adequacy receipt is outside selected task state'])
        captured=receipt.get('bindings',{}).get('verification_context_sha256')
        contexts=current.get('verification_context_sha256s') or (current.get('verification_context_sha256'),)
        if captured is None or captured not in contexts:
            return _outcome('BLOCKED',['Test adequacy verification context is stale'])
        bindings={'fingerprint':current['fingerprint'],'contract_sha256':current['contract_hash'],
            'proposal_sha256':current['proposal_hash'],'plan_sha256':current['plan_hash'],
            'check_sha256':current['check_hash'],'workspace_id':current['workspace_id'],
            'verification_context_sha256':captured}
        verified=verify_receipt(receipt,expected_bindings=bindings)
        if verified['evidence_kind']!='independent_cli':
            return _outcome('BLOCKED',['Protocol fixtures are not independent test adequacy judgment'])
        identity=verified.get('request_identity',{})
        from .bugfix import assessment_schema, assess
        schema=assessment_schema(ADEQUACY_SCHEMA,contract,captured_stage,plan=plan)
        wanted={'role':'review','purpose':'test_adequacy','stage_id':captured_stage,
            'schema_sha256':hash_value(schema),'project_id':self.store.project_id,
            'test_plan_sha256':plan['test_plan_sha256']}
        if not set(wanted).issubset(identity) or any(identity.get(field)!=value for field,value in wanted.items()):
            return _outcome('BLOCKED',['Independent test adequacy request purpose/schema/project/stage/plan differs'])
        if receipt.get('role')!='review':
            return _outcome('BLOCKED',['Independent test adequacy role differs'])
        self._independent(task_id,receipt,verified,current)
        result=verified['result']['payload']
        validation=validate_payload(result,schema)
        if validation['status']!='VALID':
            return _outcome('BLOCKED',['Invalid independent test adequacy result: '+str(validation['errors'])])
        bundle=read_record(self.store.db,'checks',task_id)['payload']
        if not bundle or hash_value(bundle)!=current['check_hash'] or bundle.get('source_changed_during_checks'):
            return _outcome('BLOCKED',['Test adequacy actual checks missing, changed or stale'])
        specs=current['checks']
        profile=evaluate_contract_profiles(contract,specs,bundle.get('results',[]),stage_id=stage_id)
        files=self._source_files(receipt)
        by_id={row['id']:row for row in plan['scenarios']}
        rows=result['scenarios']
        ids=[row['id'] for row in rows]
        if len(ids)!=len(set(ids)) or not set(ids).issubset(by_id):
            return _outcome('BLOCKED',['Independent test adequacy scenario IDs are unknown or duplicated'])
        active=[row for row in plan['scenarios'] if stage_id is None or stage_id in _compatible_stages(contract,execution['checks'],row)]
        reasons=list(result['missing_coverage']); rejected=[]
        def assessment(*args):
            return self._assessment(*args,observations=bundle.get('results',[]))
        for scenario in active:
            matches=[row for row in rows if row['id']==scenario['id']]
            if not matches:
                if scenario['required']:
                    reasons.append('Required test adequacy scenario missing: '+scenario['id'])
                continue
            if scenario['required']:
                assessment(matches[0],scenario['id'],set(scenario['evidence_ids']),specs,profile,files,reasons,rejected)
        for index,row in enumerate(result['additional_scenarios']):
            for field in ('risk','boundary','expected'):
                text(row[field],field,limit=8000)
            assessment(row,'additional risk '+str(index+1),{spec['id'] for spec in specs},specs,profile,files,reasons,rejected)
        assess(contract,plan,result,stage_id,files,by_id,specs,profile,assessment,reasons,rejected,captured_stage=captured_stage)
        return _outcome('FAIL' if rejected else 'BLOCKED' if reasons else 'PASS',reasons,rejected=rejected)

    def _assessment(self,row,label,allowed,specs,profile,files,reasons,rejected,*,observations=()):
        text(row['reason'],'assessment reason',limit=8000)
        if row['status']=='FAIL':
            rejected.append('Test inadequacy '+label+': '+row['reason'])
            rejected.extend(label+': '+gap for gap in row['missing_coverage'])
            return
        if row['status']!='PASS':
            reasons.append('Test adequacy scenario unproved: '+label)
        reasons.extend(label+': '+gap for gap in row['missing_coverage'])
        if row['status']!='PASS':
            return
        if not row['evidence']:
            reasons.append('Executed test proof missing: '+label)
        by_id={spec['id']:spec for spec in specs}
        for proof in row['evidence']:
            for field in ('check_id','test_source','test_id','assertion'):
                text(proof[field],field,limit=8000)
            check_id=proof['check_id']
            spec=by_id.get(check_id,{})
            if check_id not in allowed or spec.get('kind') not in COMMAND_KINDS:
                reasons.append('Scenario evidence is not a mapped current executed check: '+label+'/'+check_id)
                continue
            path=proof['test_source']
            if ('\\' in path or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()
                    or '..' in PurePosixPath(path).parts or path.startswith('_ase_') or path not in files):
                reasons.append('Observable test source missing from captured packet: '+label+'/'+path)
                continue
            outcome=_case_execution(proof,spec,observations,files[path]['content'],self.store.repo)
            if outcome=='FAIL':
                rejected.append('Cited test case failed in current executed check: '+label+'/'+proof['test_id'])
                continue
            if profile['by_id'].get(check_id,{}).get('status')!='PASS':
                reasons.append('Scenario evidence is not a successful current executed check: '+label+'/'+check_id)
            if outcome!='PASS':
                reasons.append('Successful cited case execution unproved (verbose unittest/pytest evidence required): '+label+'/'+proof['test_id'])

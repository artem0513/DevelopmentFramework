"""Functional module boundaries and independently runnable regression suites.

This catalogue is developer tooling, not the task acceptance/evidence gate.
Run ``python -B -m ase.modules`` or ``python -B -m ase.modules memory --test``.
"""
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import unittest


@dataclass(frozen=True)
class Module:
    name: str
    responsibility: str
    sources: tuple[str, ...]
    tests: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


def _module(name, responsibility, sources, tests, depends=(), skills=()):
    return Module(name, responsibility, tuple('ase/'+p for p in sources.split()),
        tuple('test_'+p+'.py' for p in tests.split()), tuple(depends), tuple(skills))


MODULES = (
    _module('storage', 'Local project identity, SQLite schema and transaction unit of work.',
        'store.py state.py projects.py project_local.py native_paths.py timezones.py',
        'state schema_v5 projects project_local project_local_state timezones'),
    _module('tasks', 'Versioned commands/queries, original scope, approval and obligations.',
        'tasks.py', 'task_contract_v2 audit_authority scope_audit partial_progress mixed_profiles', ('storage',), ('task-intake',)),
    _module('discovery', 'Relevant sources, existing examples, capabilities and blocker investigation.',
        'discovery.py source_coverage.py inspection.py',
        'discovery discovery_policy_freshness discovery_refresh_retention directory_sources inspection scoped_readiness progress_sources audit_source_evidence',
        ('storage','tasks'), ('task-discovery',)),
    _module('test-design', 'Bot-owned test design from requirements, project patterns and domain skills; no automatic generator.',
        'testing/adequacy.py', 'production_reasoning_scenarios reasoning_rule_evaluation adequacy_policy test_adequacy', ('tasks','discovery','backends'), ('task-orchestrator','quality-gate')),
    _module('implementation', 'Persist/apply scoped edits with before-image and crash reconciliation.',
        'edit_application.py local_edit_intent.py candidate_capture.py',
        'edit_application local_edit_authority local_edit_inventory local_edit_recovery candidate_capture candidate_capture_recovery audit_candidate_authority',
        ('storage','tasks')),
    _module('testing', 'Execute approved checks, capture logs and actual test counts.',
        'testing/__init__.py testing/execution.py', 'bun_counts command_log_paths secret_execution ci_runner',
        ('storage','tasks','discovery','execution')),
    _module('review', 'Run independent acceptance and review sessions against current evidence.',
        'review/__init__.py review/service.py', 'stage_history revision_history audit_prerequisite',
        ('tasks','evidence','backends','test-design'), ('independent-ac-verifier','independent-code-reviewer')),
    _module('evidence', 'Current proof/coverage, blocker evidence, policy and deterministic gate.',
        'evidence/__init__.py evidence/packets.py gates.py profiles.py action_evidence.py runtime_policy.py',
        'action_packet_evidence gate_regression profiles runtime_policy runtime_boundaries protected_authorization',
        ('storage','tasks','discovery','backends','test-design'), ('quality-gate',)),
    _module('memory', 'Dated knowledge, dependency freshness, alternatives and durable task context.',
        'knowledge.py continuity.py memory/__init__.py memory/context.py memory/conflicts.py',
        'knowledge knowledge_refresh knowledge_conflicts knowledge_scope_isolation continuity context_modules',
        ('storage','tasks'), ('project-context-manager',)),
    _module('reporting', 'Append-only work history, atomic closure and effective dated meeting projections.',
        'journal.py memory/repository.py memory/reports.py', 'journal report_projection',
        ('storage','tasks','memory'), ('time-reporter',)),
    _module('delegation', 'Native-worker registry, prerequisite DAG, exact attempts and sealed results.',
        'delegations.py delegation_artifacts.py', 'delegations delegation_artifacts delegation_crash delegation_integration',
        ('storage','tasks','evidence')),
    _module('recovery', 'Progress-based stopping, diagnostic leads and durable effect reconciliation.',
        'recovery.py operations.py', 'progress_recovery stopping stopping_operations operation_alias_gate operation_reconciliation operations process_ownership audit_recovery_identity',
        ('storage','tasks')),
    _module('execution', 'Approved command/access effects, ownership and operation observations.',
        'execution/__init__.py execution/actions.py execution/ports.py', 'access_revalidation', ('storage','tasks','recovery','access')),
    _module('backends', 'Codex/Claude/Copilot adapters, process capture, protocol and receipts.',
        'adapters/__init__.py adapters/codex.py adapters/claude.py adapters/copilot.py runner.py protocol.py',
        'adapter_security codex_adapter claude_adapter copilot_adapter runner', ('storage',)),
    _module('access', 'Encrypted local credential profiles and approved authenticated access.',
        'access.py credentials.py _http_worker.py', 'access credentials credential_cli credential_integration', ('storage','tasks')),
    _module('delivery', 'Publication fence, observed Git state, transfer and project installation.',
        'publishing.py transfer.py', 'publishing_v2 publishing_observed_inputs transfer portable_install project_install project_local_install ci_inspector',
        ('storage','tasks','evidence'), ('project-bootstrap','gh-fix-ci')),
    _module('coordination', 'CLI and workflow manager joining modules in the approved stage order.',
        '__init__.py __main__.py workflow.py modules.py coordinator.py', 'cli_v2 workflow_v2 workflow_scenarios module_boundaries reasoning_rule_installation coordinator_events',
        ('tasks','discovery','implementation','testing','review','reporting','delegation','recovery'), ('task-orchestrator',)),
    _module('legacy', 'Preserved v1 compatibility engine; no new v2 responsibilities.',
        'core.py', 'system', ('storage','backends')),
)


def get_module(name):
    for module in MODULES:
        if module.name == name:
            return module
    raise ValueError('Unknown module: '+name)


def test_files(name, *, root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    paths = [root/'tests'/file for file in get_module(name).tests]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise ValueError('Module tests unavailable in this checkout: '+', '.join(missing))
    return sorted(set(paths))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module', nargs='?')
    parser.add_argument('--test', action='store_true', help='Run this module\'s real unittest suite')
    parser.add_argument('--list-tests', action='store_true', help='List test files without executing them')
    args = parser.parse_args(argv)
    if (args.test or args.list_tests) and not args.module:
        parser.error('--test/--list-tests requires a module')
    try:
        modules = [get_module(args.module)] if args.module else list(MODULES)
        if args.test or args.list_tests:
            paths = test_files(args.module)
            if args.list_tests:
                print(json.dumps([str(p) for p in paths], indent=2))
                return 0
            suite = unittest.TestSuite()
            for path in paths:
                suite.addTests(unittest.TestLoader().discover(str(path.parent), pattern=path.name))
            if not suite.countTestCases():
                raise ValueError('Selected module has zero executable tests')
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        print(json.dumps([asdict(module) for module in modules], indent=2))
        return 0
    except ValueError as error:
        print(json.dumps({'error':str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

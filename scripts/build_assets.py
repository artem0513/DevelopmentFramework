"""Generate native role pointers from canonical skills/, without installing.

Product instructions live in skills/. Use --check to detect stale descriptors;
target installations are a separate reviewed installer operation.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWNED_SKILLS = (
    'task-orchestrator', 'task-intake', 'task-discovery', 'project-context-manager',
    'project-bootstrap', 'independent-ac-verifier', 'independent-code-reviewer',
    'quality-gate', 'communication-assistant', 'time-reporter', 'gh-fix-ci',
)
ROLES = {'ase-implementer': 'task-orchestrator', 'ase-ac-verifier': 'independent-ac-verifier',
         'ase-code-reviewer': 'independent-code-reviewer'}


def descriptions(root=ROOT):
    found = {path.parent.name for path in (root / 'skills').glob('*/SKILL.md')}
    if found != set(OWNED_SKILLS):
        raise ValueError('Canonical skills must contain exactly the 11 owned entrypoints')
    result = {}
    for skill in OWNED_SKILLS:
        text = (root / 'skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        match = re.search(r'^description:\s*(.+)$', text, flags=re.M)
        if not match:
            raise ValueError('Missing skill description: ' + skill)
        raw = match[1].strip()
        result[skill] = json.loads(raw) if raw.startswith('"') else raw
    return result


def generated_assets(root=ROOT):
    from ase.runtime_policy import build_bundle
    from ase.protocol import canonical
    descriptions_by_skill = descriptions(root)
    outputs = {}
    for role, skill in ROLES.items():
        description = descriptions_by_skill[skill]
        for target, relative in (('codex', '.agents/skills'), ('claude', '.claude/skills'),
                                 ('copilot', '.github/skills')):
            instructions = ('Read ' + relative + '/' + skill + '/SKILL.md in the selected workspace. '
                            'Preserve the original approved scope and existing permissions. '
                            'Use the local ase CLI and original evidence; report unavailable capabilities honestly. '
                            'Inspect authorized context and relevant safe read-only evidence before proposing an owner question; '
                            'distinguish retrievable facts from owner-only decisions and give a concise supported hypothesis/recommendation. '
                            'For useful independent ready work with permitted native subagents, read ' + relative +
                            '/task-orchestrator/references/parallel-work.md. One lead integrates adaptive waves while preserving '
                            'root leases, ownership and budgets; serial fallback remains available. '
                            'For managed native delegation, commit ase delegation register/dispatch before native spawn, '
                            'then attach the actual handle, seal the result and separately accept it; unknown launches require reconciliation. '
                            'Native advisory answers do not replace genuine independent Runner receipts. '
                            'Keep the original chat as lead and consume its durable ase events inbox; native return tools are optional. '
                            'For software, preserve explicit test_adequacy policy and a scenario plan, and require a separate '
                            'current adequacy assessment alongside original AC/review. Positive counts alone do not prove coverage. '
                            'This descriptor grants no access, approval or independent execution by itself.')
            if target == 'codex':
                path = root / 'agents' / (role + '.toml')
                content = ('name = ' + json.dumps(role) + '\ndescription = ' + json.dumps(description) +
                           '\ndeveloper_instructions = ' + json.dumps(instructions) + '\n')
            else:
                suffix = '.agent.md' if target == 'copilot' else '.md'
                path = root / 'templates/agents' / target / (role + suffix)
                content = ('---\nname: ' + role + '\ndescription: ' + json.dumps(description) +
                           ('\ninclude-custom-instructions: true' if target == 'copilot' else '') +
                           '\n---\n\n' + instructions + '\n')
            outputs[path] = content.encode('utf-8')
    outputs[root / 'ase/data/runtime_policy.json'] = canonical(build_bundle(root)) + b'\n'
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only generated asset consistency check')
    args = parser.parse_args(argv)
    changed = []
    for path, content in generated_assets().items():
        if not path.is_file() or path.read_bytes() != content:
            changed.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    print(json.dumps({'changed': changed, 'check': args.check}, indent=2))
    return 1 if args.check and changed else 0


if __name__ == '__main__':
    raise SystemExit(main())

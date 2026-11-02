"""Fetch a selected vendor skill at a resolved commit; never run its scripts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

REGISTRY = {
 'mastra': ('mastra-ai/skills', ['skills/mastra']),
 'angular': ('angular/skills', ['angular-developer']),
 'agent-framework': ('microsoft/agent-framework', ['skills/agent-framework-builder-dotnet']),
 'azure-dotnet': ('microsoft/skills', [f'.github/plugins/azure-sdk-dotnet/skills/{name}' for name in ('azure-ai-openai-dotnet', 'azure-search-documents-dotnet', 'azure-servicebus-dotnet')]),
 'azure-functions': ('MicrosoftDocs/Agent-Skills', ['skills/azure-functions']),
}

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack', choices=REGISTRY, required=True)
    p.add_argument('--dest', type=Path, default=Path.home() / '.agents' / 'skills')
    p.add_argument('--ref', help='Reviewed immutable commit; otherwise resolve current default branch')
    p.add_argument('--inspect', action='store_true', help='Print resolved source and SKILL.md without installation')
    p.add_argument('--method', choices=['auto', 'download', 'git'], default='auto', help='Use git sparse checkout for Windows long-path ZIP failures')
    a = p.parse_args()
    repo, paths = REGISTRY[a.stack]
    def fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'autonomous-se/0.1'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()
    ref = a.ref or json.loads(fetch(f'https://api.github.com/repos/{repo}/commits/HEAD'))['sha']
    if len(ref) != 40 or any(c not in '0123456789abcdef' for c in ref.lower()):
        raise SystemExit('--ref must be an immutable full commit SHA')
    for path in paths:
        content = fetch(f'https://raw.githubusercontent.com/{repo}/{ref}/{path}/SKILL.md').decode()
        print(json.dumps({'repo': repo, 'ref': ref, 'path': path}))
        if a.inspect:
            print(content)
    if not a.inspect:
        codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        installer = codex_home / 'skills/.system/skill-installer/scripts/install-skill-from-github.py'
        result = subprocess.run([sys.executable, str(installer), '--repo', repo, '--ref', ref, '--dest', str(a.dest), '--method', a.method, '--path', *paths], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        a.dest.mkdir(parents=True, exist_ok=True)
        lock = a.dest / ('ase-vendor-' + a.stack + '.json')
        lock.write_text(json.dumps({'repo': repo, 'commit': ref, 'paths': paths}, indent=2), encoding='utf-8')

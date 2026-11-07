"""Fetch a selected vendor skill at a resolved commit; never run its scripts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import datetime
import shutil

try:
    from .install import project_target, project_skill_config, file_target, directory_target
except ImportError:  # Direct script invocation.
    from install import project_target, project_skill_config, file_target, directory_target

REGISTRY = {
 'mastra': ('mastra-ai/skills', ['skills/mastra']),
 'angular': ('angular/skills', ['angular-developer']),
 'agent-framework': ('microsoft/agent-framework', ['skills/agent-framework-builder-dotnet']),
 'azure-dotnet': ('microsoft/skills', [f'.github/plugins/azure-sdk-dotnet/skills/{name}' for name in ('azure-ai-openai-dotnet', 'azure-search-documents-dotnet', 'azure-servicebus-dotnet')]),
 'azure-functions': ('MicrosoftDocs/Agent-Skills', ['skills/azure-functions']),
}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack', choices=REGISTRY, required=True)
    destination = p.add_mutually_exclusive_group()
    destination.add_argument('--dest', type=Path, help='Explicit skill destination')
    destination.add_argument('--project', type=Path, help='Activate vendor skills in one existing project')
    destination.add_argument('--global', dest='global_install', action='store_true', help='Explicit global vendor installation')
    p.add_argument('--ref', help='Reviewed immutable commit; otherwise resolve current default branch')
    p.add_argument('--inspect', action='store_true', help='Print resolved source and SKILL.md without installation')
    p.add_argument('--method', choices=['auto', 'download', 'git'], default='auto', help='Use git sparse checkout for Windows long-path ZIP failures')
    a = p.parse_args(argv)
    if not a.inspect and not (a.dest or a.project or a.global_install):
        p.error('Select --dest, --project or --global; inspection alone needs no destination')
    repo, paths = REGISTRY[a.stack]
    project = None
    config_write = None
    if a.project:
        project = a.project.resolve(strict=True)
        if not project.is_dir():
            p.error('--project requires an existing directory')
        a.dest = directory_target(project_target(project, project / '.agents/skills'))
        entries = [file_target(project_target(project, a.dest / Path(path).name / 'SKILL.md')) for path in paths]
        config_write = project_skill_config(project, entries)
        directory_target(project_target(project, project / '.codex/autonomous-se-backups'))
        file_target(project_target(project, a.dest / ('ase-vendor-' + a.stack + '.json')))
    elif a.global_install:
        a.dest = Path.home() / '.agents/skills'
    if a.dest is not None:
        directory_target(a.dest)
        for path in paths:
            file_target(a.dest / Path(path).name / 'SKILL.md')
        file_target(a.dest / ('ase-vendor-' + a.stack + '.json'))
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
        if project:
            project_target(project, lock)
        file_target(lock)
        lock.write_text(json.dumps({'repo': repo, 'commit': ref, 'paths': paths}, indent=2), encoding='utf-8')
        if config_write:
            config, data = config_write
            project_target(project, config)
            file_target(config)
            if config.exists() and config.read_bytes() != data:
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                backup = file_target(project_target(project, project / '.codex/autonomous-se-backups' / stamp / 'config.toml'))
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(config, backup)
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_bytes(data)


if __name__ == '__main__':
    main()

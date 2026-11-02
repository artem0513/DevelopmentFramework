"""Install owned runtime, skills and agent definitions, backing up changed files."""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import shutil
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- autonomous-se managed begin -->"
END = "<!-- autonomous-se managed end -->"


def install(home: Path, skill_home: Path):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = home / 'backups' / 'autonomous-se' / stamp
    changed = []

    def write(target, data):
        target = Path(target)
        if target.exists() and target.read_bytes() == data:
            return
        if target.exists():
            saved = backup / str(len(changed)) / target.name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed.append(str(target))

    runtime = home / 'autonomous-se' / 'runtime'
    for directory in ('ase', 'skills', 'agents', 'docs', 'templates', 'scripts'):
        for source in (ROOT / directory).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                write(runtime / source.relative_to(ROOT), source.read_bytes())
    for source in (ROOT / 'skills').rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
            write(skill_home / source.relative_to(ROOT / 'skills'), source.read_bytes())
    for source in (ROOT / 'agents').glob('*.toml'):
        write(home / 'agents' / source.name, source.read_bytes())
    launcher = home / 'bin' / 'ase.ps1'
    py = str(Path(sys.executable)).replace("'", "''")
    location = str(runtime).replace("'", "''")
    actual_user = str(Path.home()).replace("'", "''")
    installed_home = str(home).replace("'", "''")
    state_home = str(Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'AutonomousSE')
    state_ps = state_home.replace("'", "''")
    write(launcher, (f"$env:PYTHONPATH = '{location}' + [IO.Path]::PathSeparator + $env:PYTHONPATH\n"
                     f"if (-not $env:CODEX_HOME) {{ $env:CODEX_HOME = '{installed_home}' }}\n"
                     f"if (-not $env:USERPROFILE) {{ $env:USERPROFILE = '{actual_user}' }}\n"
                     f"if (-not $env:HOME) {{ $env:HOME = '{actual_user}' }}\n"
                     "$aseForward = @()\n"
                     "for ($aseIndex = 0; $aseIndex -lt $args.Count; $aseIndex++) {\n"
                     "  $aseArg = [string]$args[$aseIndex]\n"
                     "  if ($aseArg -match '^--home=[A-Za-z]$' -and $aseIndex + 1 -lt $args.Count -and (([string]$args[$aseIndex + 1]).StartsWith('\\') -or ([string]$args[$aseIndex + 1]).StartsWith('/'))) {\n"
                     "    $aseIndex++; $aseArg = $aseArg + ':' + [string]$args[$aseIndex]\n"
                     "  }\n"
                     "  $aseForward += $aseArg\n"
                     "}\n"
                     "$aseOptions = @()\n"
                     f"if (-not ($aseForward | Where-Object {{ $_ -eq '--home' -or $_ -like '--home=*' }})) {{ $aseOptions = @('--home', '{state_ps}') }}\n"
                     f"& '{py}' -m ase @aseOptions @aseForward\nexit $LASTEXITCODE\n").encode())
    shell_launcher = ('#!/bin/sh\n'
                      'if [ -z "${CODEX_HOME:-}" ]; then export CODEX_HOME=' + shlex.quote(str(home)) + '; fi\n'
                      'if [ -z "${HOME:-}" ]; then export HOME=' + shlex.quote(str(Path.home())) + '; fi\n'
                      'ase_has_home=0\nfor ase_arg do case "$ase_arg" in --home|--home=*) ase_has_home=1;; esac; done\n'
                      'if [ "$ase_has_home" = 0 ]; then set -- --home ' + shlex.quote(state_home) + ' "$@"; fi\n'
                      'PYTHONPATH=' + shlex.quote(str(runtime)) + ':"${PYTHONPATH:-}" exec ' + shlex.quote(sys.executable) + ' -m ase "$@"\n')
    write(home / 'bin' / 'ase', shell_launcher.encode())
    if sys.platform != 'win32':
        (home / 'bin' / 'ase').chmod(0o755)
    global_agents = home / 'AGENTS.md'
    current = global_agents.read_text(encoding='utf-8') if global_agents.exists() else ''
    block = (BEGIN + '\nFor software implementation tasks, use the task-orchestrator skill at '
             + str(skill_home / 'task-orchestrator' / 'SKILL.md')
             + '. Delegate independent AC verification and code review; use the evidence gate before claiming verified completion or publishing. '
             'Keep workflows proportional to risk and preserve repository instructions and user authorization.\n' + END)
    if BEGIN in current and END in current:
        before, rest = current.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        updated = before + block + after
    else:
        updated = current.rstrip() + '\n\n' + block + '\n'
    write(global_agents, updated.encode('utf-8'))
    # Separate profile; preserve user's model, sandbox and all existing config.
    write(home / 'autonomous.config.toml', b'[agents]\nenabled = true\n')
    manifest = {'installed_at': stamp, 'runtime': str(runtime), 'skills': str(skill_home),
                'backup': str(backup), 'changed': changed,
                'launcher': str(launcher), 'remote_push_enabled': False}
    (home / 'autonomous-se').mkdir(parents=True, exist_ok=True)
    (home / 'autonomous-se' / 'installation.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


def register_verifier(home, roots, state_home):
    """Register a scoped MCP host via Codex's supported configuration command."""
    codex = shutil.which('codex')
    if not codex:
        raise SystemExit('Codex executable unavailable; verifier host was not registered')
    roots = [Path(root).resolve(strict=True) for root in roots]
    if not roots or any(not root.is_dir() for root in roots):
        raise SystemExit('Supply existing --root directories for the verification host')
    config = home / 'config.toml'
    if config.exists():
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = home / 'backups/autonomous-se' / stamp / 'config.before-verifier.toml'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config, backup)
    runtime = home / 'autonomous-se/runtime'
    command = [codex, 'mcp', 'add', 'autonomous_se', '--env', 'PYTHONPATH=' + str(runtime),
               '--env', 'CODEX_HOME=' + str(home), '--', sys.executable, '-P', '-m', 'ase.host',
               '--home', str(state_home.resolve()), '--codex', str(Path(codex).resolve())]
    for root in roots:
        command += ['--root', str(root)]
    subprocess.run(command, check=True, env={**os.environ, 'CODEX_HOME': str(home)})
    # The user explicitly authorizes automatic independent verification. Grant
    # only these two scoped tools; preserve sandbox/approval policy elsewhere.
    text = config.read_text(encoding='utf-8')
    server_header = '[mcp_servers.autonomous_se]'
    server_pattern = re.compile(r'(?m)^' + re.escape(server_header) + r'\s*\n(?:(?!^\[).*(?:\n|$))*')
    server_match = server_pattern.search(text)
    if not server_match:
        raise SystemExit('Native MCP registration did not create the expected server table')
    section = server_match.group()
    for key, value in [('enabled_tools', '["verification_start", "verification_status"]'),
                       ('default_tools_approval_mode', '"prompt"')]:
        if re.search(r'(?m)^' + key + r'\s*=', section):
            section = re.sub(r'(?m)^' + key + r'\s*=.*$', key + ' = ' + value, section)
        else:
            section = section.rstrip() + '\n' + key + ' = ' + value + '\n'
    text = text[:server_match.start()] + section + text[server_match.end():]
    for tool in ('verification_start', 'verification_status'):
        header = '[mcp_servers.autonomous_se.tools.' + tool + ']'
        pattern = re.compile(r'(?m)^' + re.escape(header) + r'\s*\n(?:(?!^\[).*(?:\n|$))*')
        match = pattern.search(text)
        if match:
            section = match.group()
            if re.search(r'(?m)^approval_mode\s*=', section):
                updated = re.sub(r'(?m)^approval_mode\s*=.*$', 'approval_mode = "approve"', section)
            else:
                updated = section.rstrip() + '\napproval_mode = "approve"\n'
            text = text[:match.start()] + updated + text[match.end():]
        else:
            text = text.rstrip() + '\n\n' + header + '\napproval_mode = "approve"\n'
    config.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--skills', type=Path, default=Path.home() / '.agents' / 'skills')
    parser.add_argument('--register-verifier', action='store_true', help='Install the verification-only MCP host for explicit roots')
    parser.add_argument('--root', action='append', type=Path, default=[])
    parser.add_argument('--state-home', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'AutonomousSE')
    args = parser.parse_args()
    if args.register_verifier and not args.root:
        parser.error('--register-verifier requires at least one --root')
    install(args.home.resolve(), args.skills.resolve())
    if args.register_verifier:
        register_verifier(args.home.resolve(), args.root, args.state_home)
